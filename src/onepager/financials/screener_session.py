"""Authenticated screener.in session (httpx login, cookie cache, optional browser)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..config import CACHE_DIR, SETTINGS

SCREENER_BASE = "https://www.screener.in"
LOGIN_URL = f"{SCREENER_BASE}/login/"
SESSION_CACHE = CACHE_DIR / "screener_session.json"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ScreenerAuthError(Exception):
    pass


def _client(*, cookies: httpx.Cookies | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": _USER_AGENT},
        cookies=cookies or httpx.Cookies(),
        follow_redirects=True,
        timeout=45,
    )


def _csrf_from_html(html: str) -> str | None:
    m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', html)
    return m.group(1) if m else None


def _save_cache(client: httpx.Client) -> None:
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "cookies": {k: v for k, v in client.cookies.items()},
    }
    SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_CACHE.write_text(json.dumps(payload, indent=2))


def _load_cache() -> httpx.Client | None:
    if not SESSION_CACHE.exists():
        return None
    try:
        data = json.loads(SESSION_CACHE.read_text())
        if "sessionid" not in (data.get("cookies") or {}):
            return None
        cookies = httpx.Cookies()
        for name, value in (data.get("cookies") or {}).items():
            cookies.set(name, value, domain="www.screener.in")
        return _client(cookies=cookies)
    except Exception:
        pass
    return None


_WIKI_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def _looks_like_commentary(html: str) -> bool:
    if len(html) < 1000:
        return False
    low = html.lower()
    # The authenticated endpoint returns an HTML fragment with editable wiki
    # sections. Anonymous/block pages can contain the same public company text,
    # so do not accept full HTML documents as valid commentary.
    if "<!doctype html" in low or "<html" in low:
        return False
    if "register - screener" in low or "already registered? login here" in low:
        return False
    if "forbidden" in low and len(html) < 500:
        return False
    return "strong upper" in low and "/wiki/company/" in low and "/edit/" in low


def session_is_valid(client: httpx.Client, *, company_id: int = 458) -> bool:
    """True when wiki Key Insights are reachable with this session."""
    try:
        r = client.get(
            f"{SCREENER_BASE}/wiki/company/{company_id}/commentary/v2/",
            headers=_WIKI_HEADERS,
        )
        if r.status_code != 200:
            return False
        return _looks_like_commentary(r.text)
    except Exception:
        return False


def login_httpx(username: str, password: str) -> httpx.Client:
    client = _client()
    page = client.get(LOGIN_URL)
    csrf = _csrf_from_html(page.text)
    if not csrf:
        raise ScreenerAuthError("Could not read CSRF token from screener.in login page")

    resp = client.post(
        LOGIN_URL,
        data={
            "csrfmiddlewaretoken": csrf,
            "next": "/",
            "username": username.strip(),
            "password": password,
        },
        headers={"Referer": LOGIN_URL, "X-CSRFToken": csrf},
    )
    if resp.status_code >= 400:
        raise ScreenerAuthError(f"screener.in login HTTP {resp.status_code}")

    if "sessionid" not in client.cookies:
        if "Please enter a correct" in resp.text or "incorrect" in resp.text.lower():
            raise ScreenerAuthError("screener.in rejected username/password")
        raise ScreenerAuthError("screener.in login did not set a session cookie")

    if not session_is_valid(client):
        raise ScreenerAuthError(
            "Logged in but Key Insights still blocked — check account or try SCREENER_LOGIN=browser"
        )
    _save_cache(client)
    return client


def login_browser(username: str, password: str) -> httpx.Client:
    """Playwright login fallback when httpx POST is blocked (captcha, etc.)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScreenerAuthError(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        ) from e

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_USER_AGENT)
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="networkidle")
            page.fill('input[name="username"]', username.strip())
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_timeout(2500)
            if "login" in page.url.lower() and page.locator("text=Please enter a correct").count():
                browser.close()
                raise ScreenerAuthError("screener.in rejected username/password (browser login)")
            cookies = context.cookies()
            browser.close()
    except ScreenerAuthError:
        raise
    except Exception as e:
        raise ScreenerAuthError(
            "Browser login failed. Run `playwright install chromium` or set SCREENER_LOGIN=httpx."
        ) from e

    jar = httpx.Cookies()
    for c in cookies:
        if "screener.in" in (c.get("domain") or ""):
            jar.set(c["name"], c["value"], domain=c.get("domain") or "www.screener.in")
    client = _client(cookies=jar)
    if not session_is_valid(client):
        raise ScreenerAuthError("Browser login succeeded but Key Insights still blocked")
    _save_cache(client)
    return client


def get_screener_client(*, force_login: bool = False) -> httpx.Client | None:
    """Return an authenticated client if credentials/config exist; else None."""
    if not force_login:
        cached = _load_cache()
        if cached is not None:
            return cached

    username = (SETTINGS.screener_username or "").strip()
    password = SETTINGS.screener_password or ""
    if not username or not password:
        return None

    mode = (SETTINGS.screener_login or "httpx").lower()
    if mode == "browser":
        return login_browser(username, password)
    try:
        return login_httpx(username, password)
    except ScreenerAuthError as httpx_err:
        if mode in ("auto", "browser"):
            try:
                return login_browser(username, password)
            except ScreenerAuthError:
                raise httpx_err
        raise


def fetch_commentary_html(company_id: int, client: httpx.Client | None = None) -> tuple[str, str]:
    """Fetch Key Insights wiki HTML. Returns (html, via)."""
    url = f"{SCREENER_BASE}/wiki/company/{company_id}/commentary/v2/"
    if client is not None:
        r = client.get(url, headers=_WIKI_HEADERS)
        if r.status_code == 200 and _looks_like_commentary(r.text):
            return r.text, "session"
        return r.text, "blocked"
    r = httpx.get(url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True, timeout=30)
    return r.text, "anonymous"


def clear_session_cache() -> None:
    if SESSION_CACHE.exists():
        SESSION_CACHE.unlink()

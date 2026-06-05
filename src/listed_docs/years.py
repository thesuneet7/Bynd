"""Financial-year window helpers (last N years)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class YearWindow:
    years: tuple[int, ...]  # calendar years used on BSE annual report API (e.g. 2023, 2024, 2025)

    @property
    def min_year(self) -> int:
        return min(self.years)

    @property
    def max_year(self) -> int:
        return max(self.years)

    def contains_report_year(self, year: int | str) -> bool:
        try:
            return int(year) in self.years
        except (TypeError, ValueError):
            return False

    def contains_nse_span(self, from_yr: str, to_yr: str) -> bool:
        try:
            end = int(to_yr)
            start = int(from_yr)
        except (TypeError, ValueError):
            return False
        return any(start <= y <= end or y == end for y in self.years)


def current_india_fy_end_year(*, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 4 else now.year - 1


def last_n_report_years(n: int = 3, *, now: datetime | None = None) -> YearWindow:
    """BSE/NSE annual-report listing years for the latest `n` filed reports."""
    now = now or datetime.now(timezone.utc)
    fy_end = current_india_fy_end_year(now=now)
    # Before Jul, the newest filed AR is usually for the prior FY (BSE `year` field).
    latest_report_year = fy_end if now.month >= 7 else fy_end - 1
    return YearWindow(tuple(range(latest_report_year - n + 1, latest_report_year + 1)))


def nse_from_date(window: YearWindow) -> str:
    return f"01-04-{window.min_year - 1}"


def nse_to_date(window: YearWindow) -> str:
    end = window.max_year + 1
    return f"31-03-{end}"


def bse_from_date(window: YearWindow) -> str:
    return f"{window.min_year - 1}0401"


def bse_to_date(window: YearWindow) -> str:
    return f"{window.max_year + 1}0331"

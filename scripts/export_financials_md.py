#!/usr/bin/env python3
"""Thin wrapper — prefer: company-scrape --demo"""
from company_scrape.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["--demo"]))

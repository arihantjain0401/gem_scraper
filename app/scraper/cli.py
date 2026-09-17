"""Command-line entry, same behavior as the original gem_scraper.py.

Usage:
    python3 -m app.scraper.cli                       # keywords from config.json
    python3 -m app.scraper.cli --keyword laptop --keyword "500 mbps"
"""

import argparse
import sys

from app.config import load_config
from app.scraper.pipeline import run_scrape
from app.scraper.util import log, validate_keyword


def main():
    parser = argparse.ArgumentParser(description="Scrape GeM BidPlus bids by keyword.")
    parser.add_argument("--keyword", action="append", default=None,
                        help="Keyword to search (repeatable; overrides config.json).")
    args = parser.parse_args()

    config = load_config()
    keywords = args.keyword if args.keyword else config.get("keywords", [])
    keywords = [k for k in (validate_keyword(k) for k in keywords) if k]
    if not keywords:
        log("No keywords configured. Add them to config.json or pass --keyword.")
        sys.exit(1)

    run_scrape(keywords, write_csv=True)


if __name__ == "__main__":
    main()

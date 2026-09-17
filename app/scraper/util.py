"""Small shared helpers preserved from the original scraper."""

import re
from datetime import datetime

INVALID_KEYWORD_CHARS = re.compile(r"[~`!#$%\^*@+=\[\]\\;{}|\":<>?]")


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def unwrap(value):
    """The API returns most scalar fields as single-element lists. Return the scalar."""
    if isinstance(value, list):
        return value[0] if value else ""
    return value if value is not None else ""


def validate_keyword(keyword):
    """Trim and validate one keyword; returns the clean keyword or None."""
    keyword = keyword.strip()
    if len(keyword) < 3:
        log("  skipping keyword '%s': the site requires at least 3 characters." % keyword)
        return None
    if INVALID_KEYWORD_CHARS.search(keyword):
        log("  skipping keyword '%s': contains characters the site rejects." % keyword)
        return None
    return keyword

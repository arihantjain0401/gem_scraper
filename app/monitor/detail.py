"""On-demand bid detail fetching: GET showbidDocument/<b_id>, parse, cache.

Fetched only when a caller asks for it (MCP get_bid_detail, or the weekly
disappeared-resolution) — never bulk-crawled. Parsing is stdlib
html.parser + regexes (probe gate E confirms whether this suffices; the
fallback is beautifulsoup4, which would be the only new dependency).

Cache semantics in bid_details:
  - ok=1: fresh for monitor_detail_fresh_seconds (default 24h), unless force
  - ok=0: retry allowed after monitor_detail_failed_retry_seconds (default 6h)
  - raw_html is stored only when parsing failed (debugging aid)
"""

import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

from app.monitor import db as monitor_db
from app.config import BID_URL, DETAIL_URL, LIST_URL
from app.scraper.util import log

# Process-wide spacing between consecutive detail GETs (politeness).
_LAST_FETCH = {"ts": 0.0}

_CORRIGENDUM_RE = re.compile(
    r"Modified On\s*[:]?\s*(\d{2}[-/]\d{2}[-/]\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?)"
    r"\s*(.{0,200})",
    re.IGNORECASE,
)
_LOCATION_KEYS = ("location", "consignee", "deliver", "destination", "address")


class TableTextParser(HTMLParser):
    """Collect every <table>'s rows as cell lists plus the full visible text.

    Key/value tables become dict entries; the full text feeds the
    corrigendum and location regexes. <script>/<style> content is skipped
    entirely (it is CDATA, and its text otherwise leaks into the regexes).
    """

    def __init__(self):
        super().__init__()
        self.tables = []          # list of list-of-cells (strings)
        self._table_depth = 0
        self._row = None
        self._cell = None
        self._text = []           # document-order visible text
        self._buf = []
        self._skip_depth = 0      # inside <script>/<style>

    def _flush_buf(self):
        if self._buf:
            self._text.append("".join(self._buf).strip())
            self._buf = []
            self._text.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
            self.set_cdata_mode(tag)   # JS/CSS content is CDATA, not markup
            return
        if tag == "table":
            self._table_depth += 1
            self._flush_buf()
        elif tag == "tr" and self._table_depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_buf()

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_buf()

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._cell is not None:
            self._cell.append(text)
        self._buf.append(data if self._buf else data.lstrip())

    def full_text(self):
        self._flush_buf()
        return "".join(self._text)


def _key_values_from_tables(rows):
    """Rows of >=2 cells become key/value pairs; later tables win per key."""
    pairs = {}
    for row in rows:
        if len(row) < 2:
            continue
        key = re.sub(r"\s+", " ", row[0]).strip(" :")
        value = re.sub(r"\s+", " ", " ".join(row[1:])).strip()
        if key and value and key.lower() not in ("sl", "no", "s.no", "sno"):
            pairs[key] = value
    return pairs


def _parse_location(key_values, text):
    for key, value in key_values.items():
        if any(token in key.lower() for token in _LOCATION_KEYS):
            return value
    match = re.search(
        r"(?:Location|Consignee|Deliver (?:to|at)|Destination)\s*[:]\s*(.{0,200})",
        text, re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _parse_corrigenda(text):
    entries = []
    for match in _CORRIGENDUM_RE.finditer(text):
        entries.append({"date": match.group(1), "text": match.group(2).strip()})
    return entries


def _parse_specifications(tables):
    """Rows whose first cell mentions item/quantity/qty/product/description."""
    specs = []
    for row in tables:
        if len(row) >= 2 and re.search(
            r"item|qty|quantity|product|description|specification",
            row[0], re.IGNORECASE,
        ):
            specs.append(row)
    return specs


def parse_detail_html(html):
    """Return {location, corrigenda, key_values, specifications, critical_dates}."""
    parser = TableTextParser()
    parser.feed(html)
    parser.close()
    key_values = _key_values_from_tables(parser.tables)
    critical_dates = {
        key: value for key, value in key_values.items()
        if re.search(r"date|open|close|start|end", key, re.IGNORECASE)
        and re.search(r"\d{2}[-/]\d{2}[-/]\d{4}", value)
    }
    return {
        "location": _parse_location(key_values, parser.full_text()),
        "corrigenda": _parse_corrigenda(parser.full_text()),
        "key_values": key_values,
        "specifications": _parse_specifications(parser.tables),
        "critical_dates": critical_dates,
    }


def _http_get(url, config):
    """GET with retries (linear backoff, same shape as post_page_with_retry).

    Returns (status, html). Raises RuntimeError after max_retries.
    """
    headers = {
        "User-Agent": config["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": LIST_URL,
        "X-Requested-With": "XMLHttpRequest",
    }
    last_err = None
    for attempt in range(config["max_retries"]):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=config["timeout_seconds"]) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as err:
            last_err = err
            if attempt < config["max_retries"] - 1:
                time.sleep(config["request_delay_seconds"] * (attempt + 1))
    raise RuntimeError("detail GET failed after retries: %s" % last_err)


def _throttle(config):
    """Enforce monitor_detail_delay_seconds between consecutive detail GETs."""
    elapsed = time.monotonic() - _LAST_FETCH["ts"]
    if _LAST_FETCH["ts"] and elapsed < config["monitor_detail_delay_seconds"]:
        time.sleep(config["monitor_detail_delay_seconds"] - elapsed)
    _LAST_FETCH["ts"] = time.monotonic()


def _empty_parse(parsed):
    return not (parsed["location"] or parsed["corrigenda"]
                or parsed["key_values"] or parsed["specifications"])


def fetch_and_cache(b_id, config, force=False):
    """Get (possibly cached) parsed detail for one bid. The MCP tool's core.

    Always includes the stored vitals row (zero GeM cost) plus the parsed
    detail-page content when a fetch happens. Returns {bid_id, cached,
    fetched_at, ok, http_status, url, surface, vitals, detail}. 'cached'
    is True when no GeM call was made.
    """
    vitals = monitor_db.get_bid(b_id)
    cached = monitor_db.get_detail(b_id)
    now = monitor_db.utcnow_iso()
    if cached and not force:
        if cached["ok"] and cached["fetched_at"] >= _fresh_since(
                config["monitor_detail_fresh_seconds"]):
            return _cached_result(b_id, cached, ok=True, vitals=vitals)
        if not cached["ok"] and cached["fetched_at"] >= _fresh_since(
                config["monitor_detail_failed_retry_seconds"]):
            return _cached_result(b_id, cached, ok=False, vitals=vitals)

    url = DETAIL_URL + str(b_id)
    _throttle(config)
    try:
        status, html = _http_get(url, config)
        parsed = parse_detail_html(html)
        surface = "js_shell" if _empty_parse(parsed) else "static_html"
        if surface == "js_shell":
            log("monitor: detail for %s is a JS shell (no static content) "
                "— see FUTURE_TASKS.md" % b_id)
        monitor_db.set_detail(b_id, ok=True, http_status=status, url=url,
                              parsed={"surface": surface, **parsed})
        return {
            "bid_id": b_id, "cached": False, "fetched_at": now,
            "ok": True, "http_status": status, "url": url,
            "surface": surface, "vitals": vitals, "detail": parsed,
        }
    except (RuntimeError, OSError) as err:
        monitor_db.set_detail(b_id, ok=False, http_status=None, url=url,
                              parsed=None, raw_html=None)
        log("monitor: detail fetch FAILED for %s: %s" % (b_id, err))
        return {
            "bid_id": b_id, "cached": False, "fetched_at": now,
            "ok": False, "http_status": None, "url": url,
            "surface": None, "vitals": vitals, "detail": None,
            "error": str(err),
        }


def _fresh_since(seconds):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _cached_result(b_id, cached, ok, vitals=None):
    import json
    detail = json.loads(cached["parsed"]) if cached["parsed"] else None
    surface = detail.get("surface") if isinstance(detail, dict) else None
    result = {
        "bid_id": b_id, "cached": True, "fetched_at": cached["fetched_at"],
        "ok": ok, "http_status": cached["http_status"], "url": cached["url"],
        "surface": surface, "vitals": vitals, "detail": detail,
    }
    if not ok:
        result["error"] = "previous fetch failed (cached result)"
    return result


if __name__ == "__main__":  # pragma: no cover
    from app.config import load_config
    cfg = load_config()
    monitor_db.init()
    print(fetch_and_cache("GEM/2025/B/5458381", cfg, force=True))

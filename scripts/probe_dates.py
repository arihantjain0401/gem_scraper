"""One-off probe: can we filter by publication/creation date?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import urllib.parse
import urllib.request

from app.config import load_config
from app.scraper.session import GemSession

cfg = load_config()
s = GemSession(cfg["user_agent"])
s.refresh()
token = s.token


def post(url, data):
    body = urllib.parse.urlencode(data)
    req = urllib.request.Request(url, data=body.encode(), headers={
        "User-Agent": cfg["user_agent"],
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://bidplus.gem.gov.in/advance-search",
        "X-Requested-With": "XMLHttpRequest"})
    with s.opener.open(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


payload_today = json.dumps({
    "page": 1,
    "param": {"searchBid": "", "searchType": "fullText"},
    "filter": {
        "bidStatusType": "ongoing_bids", "byType": "all", "highBidValue": "",
        "byEndDate": {"from": "", "to": ""},
        "byStartDate": {"from": "01-09-2026", "to": "01-09-2026"},
        "sort": "Bid-End-Date-Oldest",
    },
}, separators=(",", ":"))

# 1) all-bids-data with an extra byStartDate filter (creation date?)
ok, n, docs = s.post_page_with_retry("", 1, cfg, filt_overrides={
    "sort": "Bid-End-Date-Oldest",
    "byStartDate": {"from": "01-09-2026", "to": "01-09-2026"}})
print("all-bids-data + byStartDate today -> ok", ok, "numFound", n)
if docs:
    print("  sample: bid_no", docs[0].get("b_bid_number"),
          "start:", str(docs[0].get("final_start_date_sort"))[:10],
          "end:", str(docs[0].get("final_end_date_sort"))[:10])

# 2) /advance-search endpoint, form style
for label, data in [
    ("advance form",
     {"csrf_bd_gem_nk": token, "date_from": "01-09-2026",
      "date_to": "01-09-2026", "bidStatusType": "ongoing_bids"}),
    ("advance payload",
     {"payload": payload_today, "csrf_bd_gem_nk": token}),
]:
    try:
        out = post("https://bidplus.gem.gov.in/advance-search", data)
        print(label, "-> len", len(out), "head:", repr(out[:150]))
    except Exception as e:
        print(label, "-> ERR", type(e).__name__, e)

# 3) all-bids-data with byEndDate window covering today only (sanity on formats)
ok, n, docs = s.post_page_with_retry("", 1, cfg, filt_overrides={
    "sort": "Bid-End-Date-Oldest",
    "byEndDate": {"from": "01-09-2026", "to": "02-09-2026"}})
print("byEndDate today..tomorrow -> ok", ok, "numFound", n)

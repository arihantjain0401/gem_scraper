"""Field mapping: raw API doc -> 17-column CSV row -> 7-column view row.

build_row is preserved verbatim from the original gem_scraper.py. The 7-column
view mapping (Bid Number / End Date / Time / Organization / Location / Item /
Quantity) and the IST conversion are new. Location has no source field in the
GeM API (verified 2026-08-31) so it renders empty — see docs/FUTURE_TASKS.md.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import BID_URL
from app.scraper.util import unwrap

IST = ZoneInfo("Asia/Kolkata")

STATUS_LABELS = ["Not Evaluated", "Technical Evaluation", "Financial Evaluation", "Bid Award"]

CSV_COLUMNS = [
    "is_new", "bid_id", "parent_bid_id", "bid_number", "parent_bid_number",
    "title", "category", "buyer_ministry", "buyer_department", "bid_type",
    "total_quantity", "start_date", "end_date", "status", "is_rate_contract",
    "is_global_tender", "url",
]


def build_row(doc, is_new):
    bid_id = unwrap(doc.get("b_id")) or unwrap(doc.get("id"))
    status_code = unwrap(doc.get("b_buyer_status"))
    status_code = int(status_code) if str(status_code).isdigit() else 0
    status_label = STATUS_LABELS[status_code] if status_code < len(STATUS_LABELS) else str(status_code)

    title = unwrap(doc.get("b_category_name"))
    category = unwrap(doc.get("bd_category_name"))
    if title and category and category not in title:
        title = "%s (%s)" % (title, category)

    return {
        "is_new": 1 if is_new else 0,
        "bid_id": bid_id,
        "parent_bid_id": unwrap(doc.get("b_id_parent")),
        "bid_number": unwrap(doc.get("b_bid_number")),
        "parent_bid_number": unwrap(doc.get("b_bid_number_parent")),
        "title": title,
        "category": category,
        "buyer_ministry": unwrap(doc.get("ba_official_details_minName")),
        "buyer_department": unwrap(doc.get("ba_official_details_deptName")),
        "bid_type": unwrap(doc.get("b_bid_type")),
        "total_quantity": unwrap(doc.get("b_total_quantity")),
        "start_date": unwrap(doc.get("final_start_date_sort")),
        "end_date": unwrap(doc.get("final_end_date_sort")),
        "status": status_label,
        "is_rate_contract": unwrap(doc.get("is_rc_bid")),
        "is_global_tender": unwrap(doc.get("ba_is_global_tendering")),
        "url": BID_URL + str(bid_id),
    }


def split_ist(iso):
    """Split an ISO UTC timestamp (e.g. '2026-08-31T13:00:00Z') into (date, time)
    strings in Asia/Kolkata. Malformed/missing values degrade to empty strings."""
    if not iso:
        return "", ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(IST)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return "", ""


def build_view_row(row):
    """Map a 17-column row (from build_row) to the 7 user-facing columns."""
    end_date_ist, end_time_ist = split_ist(row["end_date"])
    return {
        "Bid Number": row["bid_number"],
        "End Date": end_date_ist,
        "Time": end_time_ist,
        "Organization": row["buyer_department"] or row["buyer_ministry"],
        "Location": "",
        "Item": row["title"] or row["category"],
        "Quantity": row["total_quantity"],
    }

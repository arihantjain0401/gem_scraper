"""Raw API doc -> vitals dict, fingerprint, and field diffs.

Deliberately separate from app.scraper.mapping.build_row: that mapping
composes a display title and munges status labels, which would make
snapshot diffs noisy. The monitor stores the raw API values so a diff
means exactly "the source doc changed".
"""

import hashlib
import json

from app.scraper.util import unwrap

# Every diffable key (the complete API key list documented in
# docs/FUTURE_TASKS.md, minus nothing — anything we don't store can't
# be diffed). Keys absent from a doc unwrap to "".
FINGERPRINT_FIELDS = [
    "b_bid_number",
    "b_id_parent",
    "b_bid_number_parent",
    "b_bid_type",
    "b_status",
    "b_buyer_status",
    "b_category_name",
    "bd_category_name",
    "ba_official_details_minName",
    "ba_official_details_deptName",
    "b_total_quantity",
    "final_start_date_sort",
    "final_end_date_sort",
    "is_rc_bid",
    "ba_is_global_tendering",
    "is_high_value",
    "bid_schedule",
]

# Column names in monitor.db bids table (vitals keys -> columns).
COLUMN_KEYS = {
    "b_bid_number": "bid_number",
    "b_id_parent": "parent_bid_id",
    "b_bid_number_parent": "parent_bid_number",
    "b_bid_type": "bid_type",
    "b_status": "b_status",
    "b_buyer_status": "buyer_status",
    "b_category_name": "category_name",
    "bd_category_name": "bd_category_name",
    "ba_official_details_minName": "ministry",
    "ba_official_details_deptName": "department",
    "b_total_quantity": "total_quantity",
    "final_start_date_sort": "start_date",
    "final_end_date_sort": "end_date",
    "is_rc_bid": "is_rc_bid",
    "ba_is_global_tendering": "is_global_tendering",
    "is_high_value": "is_high_value",
    "bid_schedule": "bid_schedule",
}


def to_vitals(doc):
    """Unwrap one API doc into a plain vitals dict (raw values only)."""
    return {key: str(unwrap(doc.get(key))) for key in FINGERPRINT_FIELDS}


def fingerprint(vitals):
    """sha256 of the compact JSON of the vitals — the diff key."""
    payload = json.dumps(vitals, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_fields(old_vitals, new_vitals):
    """Return {key: [old, new]} for keys whose raw value changed."""
    return {
        key: [old_vitals[key], new_vitals[key]]
        for key in FINGERPRINT_FIELDS
        if old_vitals[key] != new_vitals[key]
    }

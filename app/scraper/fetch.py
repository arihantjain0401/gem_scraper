"""Pagination loop for one keyword.

Preserved from the original gem_scraper.py (fetch_keyword), plus an optional
progress callback so the web UI can show live progress while a run is active.
"""

import math
import time

from app.scraper.session import EmptySearch
from app.scraper.util import log


def fetch_keyword(session, keyword, config, progress_cb=None):
    """Fetch all pages for one keyword and return (docs, warnings).

    progress_cb, if given, is called after every page as:
        progress_cb(keyword=..., page=..., pages=..., docs=..., num_found=...)
    """
    delay = config["request_delay_seconds"]
    max_pages = config["max_pages_per_keyword"]
    warnings = []

    try:
        ok, num_found, first_docs = session.post_page_with_retry(keyword, 1, config)
    except EmptySearch:
        log("  keyword '%s': no results (empty search on GeM)." % keyword)
        return [], []
    if not ok:
        log("  keyword '%s': FAILED to fetch first page." % keyword)
        return [], ["keyword '%s' could not be fetched" % keyword]

    total_pages = math.ceil(num_found / 10) if num_found else 0
    pages_to_fetch = min(total_pages, max_pages)
    log("  keyword '%s': %d hits, fetching %d page(s)" % (keyword, num_found, pages_to_fetch))

    docs = list(first_docs)
    skipped = 0
    if progress_cb:
        progress_cb(keyword=keyword, page=1, pages=pages_to_fetch,
                    docs=len(docs), num_found=num_found)
    for page in range(2, pages_to_fetch + 1):
        time.sleep(delay)
        ok, _, page_docs = session.post_page_with_retry(keyword, page, config)
        if ok:
            docs.extend(page_docs)
        else:
            skipped += 1
            log("  WARNING: '%s' page %d could not be fetched after retries; skipping."
                % (keyword, page))
        if progress_cb:
            progress_cb(keyword=keyword, page=page, pages=pages_to_fetch,
                        docs=len(docs), num_found=num_found)

    if skipped:
        log("  keyword '%s': %d page(s) skipped." % (keyword, skipped))
        warnings.append("keyword '%s': %d page(s) skipped" % (keyword, skipped))
    if total_pages > max_pages:
        log("  WARNING: '%s' truncated to %d of %d pages — some bids not captured."
            % (keyword, max_pages, total_pages))
        warnings.append("keyword '%s' truncated to %d of %d pages"
                        % (keyword, max_pages, total_pages))
    return docs, warnings

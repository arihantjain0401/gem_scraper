"""GeM BidPlus HTTP session: cookie jar, CSRF handshake, paged POSTs.

Preserved verbatim from the original gem_scraper.py (GemSession), with the
endpoint constants now imported from app.config.
"""

import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import DATA_URL, LIST_URL


class EmptySearch(Exception):
    """GeM returns HTTP 404 for keywords with zero matches.

    This is a deterministic "no results" signal, not a transient failure —
    callers should treat it as an empty result set, not retry it.
    """


class GemSession:
    """Holds the opener/cookie-jar and the CSRF token, and can refresh the session."""

    def __init__(self, user_agent):
        self.user_agent = user_agent
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )
        self.opener.addheaders = [
            ("User-Agent", user_agent),
            ("Accept", "application/json, text/javascript, */*; q=0.01"),
        ]
        self.token = None

    def refresh(self, timeout=30):
        """GET the listing page to obtain the CSRF token and session cookie."""
        req = urllib.request.Request(LIST_URL, headers={"User-Agent": self.user_agent})
        with self.opener.open(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        match = re.search(r"csrf_bd_gem_nk['\"]?\s*[:=]\s*['\"]?([0-9a-f]{16,})", html)
        if match:
            self.token = match.group(1)
            return
        for cookie in self.cj:
            if cookie.name == "csrf_gem_cookie":
                self.token = cookie.value
                return
        raise RuntimeError("Could not locate CSRF token on the listing page.")

    def post_page(self, keyword, page, timeout, filt_overrides=None, param_overrides=None):
        """POST one page of results and return (num_found, docs).

        filt_overrides (optional) is merged into the filter dict, letting the
        monitor vary sort / byEndDate / bidStatusType; param_overrides is
        merged into the search param (e.g. searchType ""). None = identical
        behavior to the original keyword scraper.
        """
        param = {"searchBid": keyword, "searchType": "fullText"}
        if param_overrides:
            param.update(param_overrides)
        filt = {
            "bidStatusType": "ongoing_bids",
            "byType": "all",
            "highBidValue": "",
            "byEndDate": {"from": "", "to": ""},
            "sort": "Bid-End-Date-Oldest",
        }
        if filt_overrides:
            filt.update(filt_overrides)
        payload = json.dumps({"page": page, "param": param, "filter": filt}, separators=(",", ":"))
        body = urllib.parse.urlencode({"payload": payload, "csrf_bd_gem_nk": self.token})
        req = urllib.request.Request(
            DATA_URL,
            data=body.encode("utf-8"),
            headers={
                "User-Agent": self.user_agent,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": LIST_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                # GeM signals "no matches" with an HTTP 404.
                raise EmptySearch()
            raise
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Non-JSON (e.g. CodeIgniter CSRF error page) signals a stale session.
            raise RuntimeError("non-JSON response (likely expired session/CSRF)")
        if data.get("code") != 200:
            raise RuntimeError("API returned code=%s" % data.get("code"))
        response = data["response"]["response"]
        return response.get("numFound", 0), response.get("docs", [])

    def post_page_with_retry(self, keyword, page, config, filt_overrides=None,
                             param_overrides=None):
        """POST a page, retrying with a session refresh on transient failures.

        Returns (ok, num_found, docs). On persistent failure returns (False, 0, []).
        filt_overrides / param_overrides pass through to post_page (see there).
        """
        last_err = None
        for attempt in range(config["max_retries"]):
            try:
                num_found, docs = self.post_page(
                    keyword, page, config["timeout_seconds"],
                    filt_overrides=filt_overrides, param_overrides=param_overrides,
                )
                return True, num_found, docs
            except (RuntimeError, OSError) as err:
                last_err = err
                if attempt < config["max_retries"] - 1:
                    time.sleep(config["request_delay_seconds"] * (attempt + 1))
                    try:
                        self.refresh()
                    except (RuntimeError, OSError):
                        pass
        return False, 0, []

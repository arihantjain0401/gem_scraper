# Future tasks

Running log of known gaps and ideas. Add entries here before starting new work
so the next session has context.

## Known gaps

- **Location column is empty.** Verified 2026-08-31: the BidPlus API doc has no
  location field (full key list checked: `b_created_by, b_bid_number,
  b_bid_to_ra, b_bid_type, b_buyer_status, b_cat_id, b_category_name,
  b_eval_type, b_id, b_is_bunch, b_is_custom_item, b_is_inactive, b_ra_to_bid,
  b_status, b_total_quantity, b_type, ba_is_global_tendering,
  ba_is_single_packet, ba_official_details_deptName, ba_official_details_minName,
  bd_category_name, bid_schedule, final_end_date_sort, final_start_date_sort,
  id, is_high_value, is_rc_bid, parent_bid_schedule`). Options: (a) fetch each
  bid's detail page (`showbidDocument/<id>`) and parse the location, (b) LLM
  extraction from the title, (c) leave empty.
- **Item is the raw bid title** (with category suffix). Fine for now; an LLM
  pass could produce cleaner item names — but per user decision the LLM step is
  summary-only for now.

## Ideas

- **Static ngrok domain** (paid plan) so the public URL survives restarts.
- **Password change UI** — currently the password is a file edit via SSH
  (`credentials/web_password.env`).
- **Email alerts** for new bids (the news-digest project has a working Gmail
  SMTP pattern to copy).
- **xlsx export** — openpyxl is not installed; gspread writes straight to the
  API so Excel export was skipped.
- **Migrate `seen_bids.sqlite` into `data/app.db`** so all state is in one file.
- **Per-task delay/page-cap overrides** in the tasks table (currently global
  `config.json`).
- **Unit tests** with mocked GeM responses around `session.py` / `pipeline.py`.
- **Schedule editing in the UI** — deliberately excluded; only keywords are
  editable. Reschedule = `scripts/manage_task.py create` with the same task id.
- **Partial status** — pipeline warnings exist but web/scheduler runs still end
  `completed` when pages were skipped; a `partial` status could be derived from
  warnings.
- **Detail-page enrichment needs a headless browser (monitor, probe gate E FAIL,
  2026-09-01).** Verified live: `showbidDocument/<numeric_b_id>` returns 200
  with an **empty body**, and `bidding/bid/getBidResultView/<b_id>` is a **JS
  shell** — no bid data in the static HTML; everything loads via AJAX sub-
  endpoints (`bidding/buyer/getConsignees/{bid_id}`,
  `bidding/buyer/getCaDocView/`, `bidding/showSpecs/`) that need buyer-session
  params (processID / variantID / itemID) not present for anonymous viewers and
  that returned empty for ongoing bids. The listing JSON remains the only
  reliable data source for vitals; the `bid_schedule` field is only schedule-ID
  lists (no dates). Options for later: (a) Playwright/Selenium render of the
  detail page, (b) reverse-engineer the AJAX data flow with a logged-in buyer
  session, (c) skip locations/corrigenda. The monitor's `get_bid_detail` marks
  `surface: "js_shell"` when the fetch yields nothing — the pipeline is wired,
  only the source is missing.

## Done

- 2026-08-31: App rebuild — scraper split into `app/scraper/`, Flask UI,
  launchd scheduling (daily 06:30 task with 6 keywords), Google Sheets via
  dedicated service account, DeepSeek summary.
- 2026-08-31: DeepSeek key handling changed from per-use prompt to stored key
  in `credentials/deepseek.env` (user request).
- 2026-08-31: Password login + login audit log (IP/device), and ngrok public
  tunnel with `X-Client-Ip` traffic-policy injection (waitress strips the
  standard X-Forwarded-* headers).
- 2026-08-31: Consolidated to a single launchd job — `com.gemscraper.web` runs
  the website, the in-app scheduler (replaces per-task launchd plists), and
  the ngrok tunnel as a supervised child. Brute-force throttle + session TTL
  added to the login.
- 2026-08-31: Multi-user login (username + password, `users` table, roles):
  admin `arihant` gets everything; limited users get extract + view only.
  User management via `scripts/manage_users.py`.
- 2026-08-31: Scheduled runs auto-save to Google Sheets on completion
  (`sheets.auto_save_run`); ad-hoc runs stay manual. Empty-keyword handling:
  GeM returns HTTP 404 for zero-match searches — now logged as "no results"
  instead of a FAILED warning (`EmptySearch` in `app/scraper/session.py`).
- 2026-09-01: **Monitor probe phase (P1-P8) run against live GeM.** Gate
  results: A full-board enumeration via empty `searchBid` **PASS** (numFound
  42,131); B delta sort **PASS** (`Bid-End-Date-Newest` puts newest first —
  all 9 sort names accepted with stable numFound); C byEndDate windows **PASS**
  (ISO and DD-MM-YYYY both filter; 36,819 bids in the next 14 days); D deep
  paging **PASS** (pages 1000/2000 return docs; beyond-last page returns empty
  docs, code 200); E detail parsing **FAIL** (JS shell — see Known gaps).
  numFound pulse: 42,131 -> 42,132 in ~30 s (indexing churn ~1 bid/min at
  evening load). Latency ~0.18 s/request. Chosen delta mechanism: convergence
  crawl under `Bid-End-Date-Newest` (persisted in `data/monitor_state.json`).
  Full report: `output/probe_report_20260901.json`.
- 2026-08-31: **Google Sheets saga resolved.** Root cause of the endless 404s:
  the original spreadsheet "BD 3.2" (`1I4aJ3ZrqMt-4h0KQ2pUHusz46b5P_ZuIYkKYwpuerxM`,
  owner defproglobal@gmail.com) has a **broken get-by-ID reference in Google's
  backend** — `drive.files.list` sees it (correct ID, owner, trashed=false),
  but `drive.files.get` (v2 and v3), `sheets.spreadsheets.get`, and every
  anonymous link path return 404 for ALL identities including the owner, while
  an already-open browser session kept rendering it. Renaming (metadata
  reindex attempt) did not heal it. Fix: a browser **File → Make a copy**
  produced "Copy of BD 3.2" (`15Poe5vXmCjvVc1hL0tGaZSEIXQynquXyf3T6ZbDwdro`),
  owned by arihantjain0401 — now `GOOGLE_SPREADSHEET_ID` in `app/config.py`.
  The app writes sheets via **Google OAuth** (Connect Google) as
  arihantjain0401; the service-account path is installed but unused (kept as
  fallback). If the original file should be repaired in place, that needs a
  Google Drive support ticket.

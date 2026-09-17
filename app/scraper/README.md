# app/scraper/

The scraping engine — pure Python stdlib, preserved from the original
`gem_scraper.py` (deleted after this split).

- `session.py` — cookie jar, CSRF handshake against bidplus.gem.gov.in, paged
  POSTs with retry/refresh (verbatim logic)
- `fetch.py` — per-keyword pagination (1.5 s politeness delay, 200-page cap)
- `mapping.py` — 17-column row + 7-column view + UTC→IST split
- `dedup.py` — `seen_bids.sqlite` is_new tracking (verbatim)
- `csv_writer.py` — 17-column dated CSVs
- `locking.py` — cross-process flock so only one scrape runs at a time
- `pipeline.py` — shared orchestration used by CLI, scheduler, and web
- `cli.py` — `python -m app.scraper.cli --keyword "..."`

Never change the handshake payload shape or the `Bid-End-Date-Oldest` sort
without re-verifying against the live site — pagination depends on them.

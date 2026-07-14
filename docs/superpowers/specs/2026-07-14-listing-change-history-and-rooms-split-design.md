# Listing Change-History + Rooms Split — Design

**Date:** 2026-07-14
**Status:** Approved (pending spec review)

## Problem

Two related gaps in the data model:

1. **`rooms` is an opaque text blob.** It stores strings like `'2BR / 1.5Ba'`, `'1BR / splitBa'`.
   You can't filter/sort/aggregate by bedroom or bathroom count cleanly — the UI currently
   groups on the raw string, producing ~25 distinct "room" categories instead of a tidy
   bedrooms × bathrooms breakdown.

2. **We keep no history of listing edits.** `listings` is written with `INSERT OR REPLACE`,
   so every re-scrape overwrites the prior snapshot. Only `price` has a time-series
   (`prices` table). When a landlord edits a description, title, availability, etc., the old
   value is lost. There's no way to answer "what did this listing change, and when."

## Goals

- Split `rooms` into structured, queryable columns while preserving the raw text.
- Record a per-listing change history for content fields, going forward.
- Surface a listing's full timeline (price + field changes) in the UI via a modal opened
  from the price-trend arrow.

## Data from the live DB (informs parsing & backfill)

- 27,705 listings; `rooms` is never NULL/empty and always has the shape `X / Y`.
- Distinct bedroom parts: `1BR, 2BR, 3BR, 4BR, 5BR` (always a plain integer; no studio, no split/shared).
- Distinct bathroom parts: `1Ba, 1.5Ba, 2Ba, 2.5Ba, 3Ba, 3.5Ba, 4Ba` (numeric incl. halves),
  plus `sharedBa` and `splitBa` (text edge cases; bathrooms only).

## Feature 1: Rooms split

### Schema (add to `listings`)

- `bedrooms REAL` — integer count as a float (e.g. `2.0`). REAL for consistency with bathrooms.
- `bathrooms REAL` — numeric count incl. halves (e.g. `1.5`); NULL when the bathroom part is text.
- `bathrooms_type TEXT` — `'split'` or `'shared'` when the bathroom part is textual; else NULL.
  When set, `bathrooms` is NULL (and vice-versa).
- **`rooms TEXT` is kept unchanged** as the raw source of truth / reference.

There is deliberately NO `bedrooms_type` column — the data shows bedrooms are always integers,
so it would be perpetually NULL (YAGNI).

### Parser (single shared helper)

Add `parse_rooms(rooms_str)` to `craigscraper/spiders/shared_utils.py` (next to the other
extractors). Returns a dict `{'bedrooms': float|None, 'bathrooms': float|None, 'bathrooms_type': str|None}`.

Logic:
- Split `rooms_str` on the first `/` into `bed_part`, `bath_part`.
- `bedrooms`: extract the leading integer from `bed_part` (strip `BR`), as float. If no digits → None.
- `bath_part` (strip `Ba`): if it parses as a float → `bathrooms`, `bathrooms_type=None`.
  Otherwise (e.g. `split`, `shared`) → `bathrooms=None`, `bathrooms_type` = the lowercased word.
- Any malformed/unsplittable input → all three None (defensive; never raise).

### Where it runs

- **Crawler** (`rent.py:parseItem`): after `item['rooms']` is set, call `parse_rooms` and set
  `item['bedrooms']`, `item['bathrooms']`, `item['bathrooms_type']`.
- **Pipeline** (`pipelines.py`): add the three columns to the `listings` column list in
  `create_table_if_not_exists` (its existing `ALTER TABLE` auto-add logic applies them to the
  live DB), and add them to the `INSERT OR REPLACE` column list + values tuple.
- **Backfill**: extend the existing idempotent backfill. For rows where `bedrooms IS NULL`,
  re-parse the stored raw `rooms` and populate the three columns. Runs on startup, no-op once
  complete. Backfills all 27,705 existing rows (fully recoverable — raw `rooms` is retained).
  Note: the current backfill helper handles the `pool/gym/parking/ev_charging` string features;
  rooms parsing sets three columns from one source and returns a dict, so it needs its own
  small backfill routine (not shoehorned into `findFeature`).

## Feature 2: Listing change-history

### Schema (new table)

```sql
CREATE TABLE IF NOT EXISTS listing_changes (
    listing_id INTEGER,
    field      TEXT,
    old_value  TEXT,
    new_value  TEXT,
    changed_at TEXT
)
```
Index: `CREATE INDEX IF NOT EXISTS listing_changes_ids ON listing_changes(listing_id)`.

No uniqueness constraint needed — each detected change is a distinct event. `changed_at` is the
ISO timestamp of the detecting scrape (use `item['last_updated']` so it aligns with the price
series and is deterministic/testable, rather than wall-clock now()).

### Tracked fields (content only)

`title`, `description`, `attributes`, `available_on`, `size`, `rooms`.

Excluded: `price` (own `prices` table), and volatile/derived fields — `distance`,
`still_published`, `last_updated`, `posted_on`, and the derived room columns
(`bedrooms`/`bathrooms`/`bathrooms_type`, since they follow `rooms`).

`attributes` is compared as its stored form (the `', '.join(...)` string) to match what's in the
DB column.

### Population

In `pipelines.py:process_item`, BEFORE the `INSERT OR REPLACE`:
1. `SELECT <tracked fields> FROM listings WHERE id = ?`.
2. If a row exists, for each tracked field where stored value ≠ incoming value, insert a
   `listing_changes` row `(id, field, old_value, new_value, item['last_updated'])`.
3. If no row exists (first-ever sighting), log nothing — a first insert is not a "change."
4. Proceed with the existing `INSERT OR REPLACE`.

**No backfill / no synthetic baseline** (per decision): history accrues from deploy forward.
Existing listings get their first `listing_changes` entries only when a real edit is next detected.

## Feature 3: UI — timeline modal on the price arrow

### Interaction

The price-trend arrow in Tab 1's property table becomes a link:
`<a href="?history=<listing_id>" ...>→ 0.0% $2100→$2100</a>`, styled to match the current
inline trend text (color + arrow preserved).

On rerun, `main()` checks `st.query_params`. If `history` is present:
1. Open an `@st.dialog`-decorated function (Streamlit 1.59 native modal) for that listing_id.
2. Inside, render the unified timeline (below).
3. Clear the param (`st.query_params.clear()` / pop) so closing the dialog or refreshing
   doesn't reopen it.

Rationale: Tab 1's table is one HTML blob (`to_html` → `st.write(unsafe_allow_html=True)`), so a
direct click→callback isn't available; the query-param anchor is the modern, contained bridge
(`st.query_params`, NOT the removed `experimental_*` APIs). This keeps the existing HTML table
(clickable titles, colored trends) unchanged.

### Modal content — unified chronological timeline

For the selected `listing_id`, merge and sort by timestamp (most recent first):
- **Price events** from `prices`: sort that listing's price rows chronologically, collapse
  consecutive identical prices (display-only dedup), then emit an event per transition
  `(last_updated_of_new, 'price', prev_price→this_price)`. The earliest price has no predecessor,
  so it is shown as an initial `(last_updated, 'price', '' → first_price)` "listed at" entry
  rather than a change. This removes the `$2100→$2100` noise from the known duplicate-price data
  issue without modifying stored rows.
- **Field events** from `listing_changes`: `(changed_at, field, old→new)`.

Render as a simple table: date · what changed · old → new. If there's no history beyond the
initial price, show a friendly "No recorded changes yet."

## Verification plan

1. **Unit test the parser** — `parse_rooms` against the real distinct values pulled from the DB:
   `'1BR / 1Ba'`→(1,1,None), `'2BR / 1.5Ba'`→(2,1.5,None), `'1BR / splitBa'`→(1,None,'split'),
   `'1BR / sharedBa'`→(1,None,'shared'), plus a malformed input → (None,None,None). This is the
   project's first parser unit test.
2. **Live-crawl smoke test** on the upgraded 3.14 stack: confirm new listings populate
   `bedrooms/bathrooms/bathrooms_type`; confirm the backfill fills existing NULL rows; confirm
   that editing a tracked field between two crawls writes a `listing_changes` row.
3. **UI check**: launch the UI against a DB copy, click a price arrow, confirm the modal opens
   with a merged timeline and that `$X→$X` duplicates are collapsed.

## Deployment

Same host/flow as prior work (see memory `deployment` and `local-dev-env`):
push to `main` → CI builds image → back up `rents.db` (`sudo cp`) → pull-by-digest (verify) →
recreate container → verify logs + UI HTTP 200. The pipeline's `ALTER TABLE` auto-add + the new
`CREATE TABLE IF NOT EXISTS` handle the live schema migration on startup; the rooms backfill runs
idempotently on that same startup.

## Out of scope (tracked separately on the fix-list)

- Price-duplicate WRITE fix (only display-side dedup is included here, in the modal).
- The `available_on` day-parsing bug (`rent.py:234`).
- The notification-transport misconfiguration.
- Any migration off Scrapy/Streamlit.

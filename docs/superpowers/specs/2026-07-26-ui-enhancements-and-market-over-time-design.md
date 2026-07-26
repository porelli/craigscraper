# UI Enhancements + Market-Over-Time Dashboard — Design

**Date:** 2026-07-26
**Status:** Approved (pending spec review)

## Problem

Three UI improvements to the Streamlit dashboard (`ui/ui.py`):

1. The Available Properties table defaults to sorting by Price ascending; the user wants
   **Size, descending** by default.
2. The feature columns (Gym/Pool/Parking/EV Charging) render literal `True`/`False` text,
   which is noisy and slow to scan.
3. The "Market Statistics" tab is a pure **snapshot** — every chart groups the *current*
   listing set by room count and never uses the time dimension, even though the `prices` table
   now holds ~18 months (33.6k rows) of clean, deduplicated dated price points. There is no way
   to see whether rent is rising/falling or how market activity changes over time.

## Data available (verified against prod)

- `prices`: 33,617 rows, 28,266 listings, span 2025-02-14 → 2026-07-26, **18 distinct months**.
- `listings.posted_on`: span 2024-12 → 2026-07 (supports new-listings-per-period).
- `listings.bedrooms`: populated for all rows (clean grouping key).
- `still_published`: 209 True / 28,057 False (rich history; small current inventory).
- Early months are sparse (2025-02 had 357 price points) → guard against noisy single-listing medians.

## Feature 1: Default sort = Size descending

In Tab 1 sort controls (`ui.py` ~line 439-440):
- "Sort by" selectbox default → `Size` (find its index in `display_df.columns` dynamically; do
  not hardcode a numeric index, since column order may shift).
- "Order" radio default → `Descending`.

User can still change both. **Known caveat (out of scope, per decision):** sorting runs on the
display DataFrame where `Price` is a formatted string (`$2,100`), so sorting by Price is lexical,
not numeric. Size is numeric, so the new default sorts correctly. Leave a code comment noting the
Price caveat; do not fix the numeric-sort issue in this work.

## Feature 2: Feature-specific emoji columns

In the display-formatting step (before `to_html`, ~line 399-404), map the four feature columns
from their `True`/`False` values to icons:

- Gym: `True → 🏋️`, `False → ''`
- Pool: `True → 🏊`, `False → ''`
- Parking: `True → 🅿️`, `False → ''`
- EV Charging: `True → ⚡`, `False → ''`

Note the DataFrame values are Python booleans (converted in `load_listings_data`), so map on
`True`/`False`, not the strings `'True'`/`'False'`. Sidebar filter checkboxes are unchanged —
only the table cells render icons. Apply after any filtering/sorting that depends on the boolean
values (sorting by these columns is not a default and not important; icons are display-only).

## Feature 3: Market-over-time dashboard (Tab 3 restructure)

Restructure Tab 3 "Market Statistics" into three sub-tabs via `st.tabs`:

### 3a — Rent over time
- **Median asking rent by month**: line chart, one series overall + one per bedroom count
  (1–4 BR; group 5+ or skip if sparse). Data: bucket `prices.last_updated` to `YYYY-MM`, join
  each price row to its listing's `bedrooms`, take the **median** price per (month, bedrooms).
  Median resists outliers better than mean.
- **Delta metric row**: current-month median vs 3 / 6 / 12 months ago, shown as `st.metric`
  with % change deltas. If a comparison month is missing, show "n/a".
- **Sparsity guard**: only plot a (month, bedroom) point when it has ≥ N underlying price rows
  (N=5) so early low-volume months don't produce single-listing "medians". Document N in a
  constant.

### 3b — Market activity
- **New listings per month**: bar chart from `count` of listings grouped by `strftime('%Y-%m', posted_on)`.
- **Inventory over time**: for each month, count listings active in that month, approximated as
  months between `posted_on` and `last_updated` inclusive. Labeled as an estimate.
- **Days-on-market trend**: line chart of average `(last_updated - posted_on)` in days, grouped by
  month of `posted_on`. **Explicitly labeled "approximate"** in the chart title/caption — we detect
  removal at next scrape and store only last-seen (`last_updated`), not a true removal timestamp
  (per decision: approximate + label, no schema change).

### 3c — Snapshot (existing content, upgraded)
- Keep the current price-distribution box plot and the price-trend pie chart, plus the existing
  "Rented Properties" content (rented price histogram, days-on-market box, avg days table).
- **Change every grouping from the raw `rooms` string to `bedrooms`** across all of these charts
  (retires the last raw-`rooms` usage in the UI; cleaner categories). Keep the existing two-way
  split within Snapshot ("Price Distribution" and "Rented Properties") as nested `st.tabs` — this
  is just the current stat_tab1/stat_tab2 content moved under the new "Snapshot" sub-tab, regrouped
  by `bedrooms`.

## Implementation notes

- New cached data helpers (`@st.cache_data(ttl=300)`), all **parameterized SQL** (consistent with
  recent fixes — no f-string interpolation):
  - `get_monthly_median_rent()` → DataFrame (month, bedrooms, median_price, n) via a SQL join of
    `prices` to `listings(bedrooms)` with `GROUP BY strftime('%Y-%m', last_updated), bedrooms`.
    Use SQLite `median`? SQLite has no built-in median — compute median in pandas after fetching
    grouped raw prices, OR fetch (month, bedrooms, price) and aggregate in pandas. Given 33k rows,
    fetching per-month-bedroom prices and taking pandas median is fine.
  - `get_new_listings_per_month()` → (month, count) from `posted_on`.
  - `get_days_on_market_by_month()` → (month, avg_days) from `posted_on`/`last_updated`.
- Monthly bucketing via `strftime('%Y-%m', <col>)` in SQL.
- All charts via plotly (`px.line`/`px.bar`/`px.box`/`px.pie`) with `width="stretch"` (current API).
- Guard every chart for empty/sparse data (show `st.info(...)` instead of rendering an empty or
  single-point chart).

## Verification

1. **Unit test** the month-bucketing + median aggregation helper logic on a small synthetic
   in-memory SQLite dataset (deterministic, no network): assert correct median per (month, bedrooms)
   and that the sparsity guard drops under-N buckets.
2. **Live Playwright check** on prod data (standard since the slider-crash lesson): open Tab 3, click
   through all three sub-tabs, confirm no `StreamlitAPIException`/exception element renders and charts
   populate. Screenshot each sub-tab.
3. Confirm Tab 1 shows Size-descending by default and emoji feature columns render.

## Deployment

Same proven flow (memory `deployment`): push to main → CI build → back up `rents.db` (`sudo cp`) →
pull-by-digest (verify) → recreate container → verify logs + Playwright UI check. No schema change,
so no migration/backfill this time.

## Out of scope

- Numeric-sort fix for the Price column (noted as a caveat; not fixed).
- True removal-timestamp schema change for accurate days-on-market (using approximation + label).
- Notification transport configuration (still pending a user-provided apprise URL).

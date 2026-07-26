# UI Enhancements + Market-Over-Time Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Default the property table to Size-descending, render feature columns as emoji, and rebuild the Market Statistics tab into a time-series dashboard (rent-over-time, market activity, snapshot) driven by the 18-month `prices` history.

**Architecture:** All changes are in `ui/ui.py` plus new pure-Python aggregation helpers and their unit tests. New cached, parameterized SQL helpers read `prices` joined to `listings.bedrooms` and `posted_on`-based aggregates; medians are computed in pandas (SQLite has no median). Tab 3 is restructured into three `st.tabs` sub-tabs. No schema change.

**Tech Stack:** Python 3.14, Streamlit 1.59 (`st.tabs`, `st.metric`, `width="stretch"`), pandas 3.0, plotly, SQLite, pytest.

## Global Constraints

- Python 3.14; run local commands with `python3.14` (system `python3` is 3.9). pytest via a throwaway venv (dev-only; NOT added to requirements).
- All new SQL uses **parameterized queries** (no f-string interpolation), consistent with recent fixes.
- Feature-column values in the DataFrame are Python booleans (`load_listings_data` maps `'True'→True`), so map on `True`/`False`, not strings.
- Emoji: Gym `🏋️`, Pool `🏊`, Parking `🅿️`, EV Charging `⚡`; `False → ''` (blank). Sidebar filter checkboxes unchanged.
- Default sort: "Sort by" → `Size`, order → `Descending`. Find the `Size` index dynamically (do not hardcode a numeric index). Leave a comment noting the pre-existing Price-sorts-as-text caveat; do NOT fix numeric sort.
- Market dashboard groups by `bedrooms` (never the raw `rooms` string) everywhere.
- Sparsity guard: only plot a (month, bedroom) median point backed by ≥ `MIN_POINTS_PER_BUCKET` (=5) price rows; define as a module constant.
- Days-on-market is approximate (`posted_on → last_updated`); every days-on-market chart must be labeled "approximate".
- Every chart guards empty/sparse data with `st.info(...)` rather than rendering an empty chart.
- Deploy via the proven flow with a DB backup; no migration (no schema change).

---

## File Structure

- **`ui/ui.py`** — MODIFY. Sort default, emoji formatting, new data helpers, Tab 3 restructure.
- **`craigscraper/market_analysis.py`** — CREATE. Pure functions for month-bucketing + median aggregation + sparsity filtering, importable and unit-testable without Streamlit/network. (Keeping the logic out of `ui.py` makes it testable — `ui.py` imports and renders.)
- **`tests/test_market_analysis.py`** — CREATE. Unit tests for the aggregation logic.

Note: `craigscraper/market_analysis.py` lives in the package but has NO scrapy/streamlit imports — just stdlib + pandas — so it imports cleanly in both the UI process and pytest.

---

## Task 1: Market-analysis aggregation helpers + unit tests

**Files:**
- Create: `craigscraper/market_analysis.py`
- Create: `tests/test_market_analysis.py`

**Interfaces:**
- Consumes: nothing (pure functions over pandas DataFrames).
- Produces:
  - `MIN_POINTS_PER_BUCKET = 5` (module constant)
  - `monthly_median_rent(prices_df) -> DataFrame[month, bedrooms, median_price, n]` — input has columns `month` (str 'YYYY-MM'), `bedrooms` (float), `price` (int); groups by (month, bedrooms), computes median price and count `n`, drops buckets with `n < MIN_POINTS_PER_BUCKET`.
  - `new_listings_per_month(posted_months) -> DataFrame[month, count]` — input is a Series/list of 'YYYY-MM' strings; returns counts per month sorted by month.
  - `pct_change_vs(median_overall_df, months_back) -> float | None` — given a DataFrame[month, median_price] (overall, one row per month, sorted), returns the % change of the latest month's median vs the median `months_back` calendar months earlier; None if that month is absent.
  - `active_listings_per_month(spans_df) -> DataFrame[month, active]` — input has columns `posted_month` ('YYYY-MM') and `last_month` ('YYYY-MM', the month of last_updated); for each listing, counts it as active in every month from `posted_month` through `last_month` inclusive, then returns the count of active listings per month sorted by month. This is the "inventory over time" curve.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market_analysis.py`:

```python
import pandas as pd
from craigscraper.market_analysis import (
    MIN_POINTS_PER_BUCKET, monthly_median_rent, new_listings_per_month,
    pct_change_vs, active_listings_per_month
)

def _prices(rows):
    return pd.DataFrame(rows, columns=['month', 'bedrooms', 'price'])

def test_monthly_median_basic():
    # 5 one-bed rows in 2025-03 -> median kept; 2 one-bed rows in 2025-04 -> dropped (< 5)
    rows = [('2025-03', 1.0, p) for p in [2000, 2100, 2200, 2300, 2400]]
    rows += [('2025-04', 1.0, 3000), ('2025-04', 1.0, 3100)]
    out = monthly_median_rent(_prices(rows))
    kept = out[(out['month'] == '2025-03') & (out['bedrooms'] == 1.0)]
    assert len(kept) == 1
    assert kept.iloc[0]['median_price'] == 2200
    assert kept.iloc[0]['n'] == 5
    # 2025-04 bucket dropped by sparsity guard
    assert out[(out['month'] == '2025-04')].empty

def test_monthly_median_separates_bedrooms():
    rows = [('2025-03', 1.0, p) for p in [2000]*5]
    rows += [('2025-03', 2.0, p) for p in [3000]*5]
    out = monthly_median_rent(_prices(rows))
    assert set(out['bedrooms']) == {1.0, 2.0}
    assert out[out['bedrooms'] == 2.0].iloc[0]['median_price'] == 3000

def test_new_listings_per_month():
    out = new_listings_per_month(pd.Series(['2025-03', '2025-03', '2025-04']))
    assert list(out['month']) == ['2025-03', '2025-04']
    assert list(out['count']) == [2, 1]

def test_pct_change_vs_present():
    df = pd.DataFrame({'month': ['2025-01', '2025-04'], 'median_price': [2000, 2200]})
    # latest is 2025-04 (2200); 3 months back is 2025-01 (2000) -> +10%
    assert round(pct_change_vs(df, 3), 1) == 10.0

def test_pct_change_vs_missing_returns_none():
    df = pd.DataFrame({'month': ['2025-04'], 'median_price': [2200]})
    assert pct_change_vs(df, 3) is None

def test_active_listings_per_month_spans_inclusive():
    # listing A active 2025-01..2025-03; listing B active 2025-02..2025-02 (posted+removed same month)
    spans = pd.DataFrame(
        [('2025-01', '2025-03'), ('2025-02', '2025-02')],
        columns=['posted_month', 'last_month']
    )
    out = active_listings_per_month(spans)
    got = dict(zip(out['month'], out['active']))
    assert got == {'2025-01': 1, '2025-02': 2, '2025-03': 1}

def test_active_listings_per_month_year_boundary():
    spans = pd.DataFrame([('2025-11', '2026-01')], columns=['posted_month', 'last_month'])
    out = active_listings_per_month(spans)
    assert list(out['month']) == ['2025-11', '2025-12', '2026-01']
    assert list(out['active']) == [1, 1, 1]
```

- [ ] **Step 2: Run the tests, verify they fail**

```bash
rm -rf /tmp/pytestenv && python3.14 -m venv /tmp/pytestenv && /tmp/pytestenv/bin/pip install -q pytest pandas
/tmp/pytestenv/bin/python -m pytest tests/test_market_analysis.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'craigscraper.market_analysis'`.

- [ ] **Step 3: Implement `craigscraper/market_analysis.py`**

```python
"""Pure aggregation helpers for the market-over-time dashboard.

No scrapy/streamlit imports so this is unit-testable and importable from the UI process.
"""
import pandas as pd

MIN_POINTS_PER_BUCKET = 5  # drop (month, bedroom) medians backed by fewer price points


def monthly_median_rent(prices_df):
    # prices_df columns: month (str 'YYYY-MM'), bedrooms (float), price (int)
    if prices_df.empty:
        return pd.DataFrame(columns=['month', 'bedrooms', 'median_price', 'n'])
    grouped = prices_df.groupby(['month', 'bedrooms'])['price']
    out = grouped.agg(median_price='median', n='count').reset_index()
    out = out[out['n'] >= MIN_POINTS_PER_BUCKET]
    return out.sort_values(['month', 'bedrooms']).reset_index(drop=True)


def new_listings_per_month(posted_months):
    s = pd.Series(list(posted_months)).dropna()
    if s.empty:
        return pd.DataFrame(columns=['month', 'count'])
    out = s.value_counts().reset_index()
    out.columns = ['month', 'count']
    return out.sort_values('month').reset_index(drop=True)


def pct_change_vs(median_overall_df, months_back):
    # median_overall_df: columns month ('YYYY-MM'), median_price; one row per month
    if median_overall_df.empty:
        return None
    df = median_overall_df.sort_values('month').reset_index(drop=True)
    latest_month = df.iloc[-1]['month']
    latest_val = df.iloc[-1]['median_price']
    year, mon = int(latest_month[:4]), int(latest_month[5:7])
    total = (year * 12 + (mon - 1)) - months_back
    target = '%04d-%02d' % (total // 12, (total % 12) + 1)
    match = df[df['month'] == target]
    if match.empty:
        return None
    prev = match.iloc[0]['median_price']
    if prev == 0:
        return None
    return (latest_val - prev) / prev * 100


def _month_to_index(month_str):
    # 'YYYY-MM' -> integer month index (year*12 + month-1) for inclusive range math
    return int(month_str[:4]) * 12 + (int(month_str[5:7]) - 1)


def _index_to_month(idx):
    return '%04d-%02d' % (idx // 12, (idx % 12) + 1)


def active_listings_per_month(spans_df):
    # spans_df columns: posted_month ('YYYY-MM'), last_month ('YYYY-MM').
    # Count each listing as active in every month from posted_month through last_month inclusive.
    if spans_df.empty:
        return pd.DataFrame(columns=['month', 'active'])
    counts = {}
    for _, r in spans_df.dropna(subset=['posted_month', 'last_month']).iterrows():
        start = _month_to_index(r['posted_month'])
        end = _month_to_index(r['last_month'])
        if end < start:  # defensive: ignore inverted spans
            continue
        for idx in range(start, end + 1):
            m = _index_to_month(idx)
            counts[m] = counts.get(m, 0) + 1
    out = pd.DataFrame(sorted(counts.items()), columns=['month', 'active'])
    return out
```

- [ ] **Step 4: Run the tests, verify they pass**

```bash
/tmp/pytestenv/bin/python -m pytest tests/test_market_analysis.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add craigscraper/market_analysis.py tests/test_market_analysis.py
git commit -m "feat(market) add month-bucketing/median aggregation helpers with tests"
```

---

## Task 2: Default sort (Size desc) + emoji feature columns

**Files:**
- Modify: `ui/ui.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no new consumers. Display-only changes in Tab 1.

- [ ] **Step 1: Emoji-format the feature columns**

In Tab 1's display-formatting block (after the date formatting, ~line 404, before building
`clickable_title`), add:

```python
            # Render feature flags as emoji (values are Python booleans from load_listings_data)
            feature_icons = {'gym': '🏋️', 'pool': '🏊', 'parking': '🅿️', 'ev_charging': '⚡'}
            for col, icon in feature_icons.items():
                display_df[col] = display_df[col].map({True: icon, False: ''})
```

(The column headers are renamed later to Gym/Pool/Parking/EV Charging as they already are.)

- [ ] **Step 2: Default the sort to Size, descending**

Replace the sort-control lines (currently ~line 439-440):

```python
            # Let user sort by any column. Default to Size, descending.
            # NOTE: 'Price' sorts lexically here because it's a formatted string ($2,100) at
            # this point — a pre-existing caveat, intentionally not fixed in this change.
            sort_options = list(display_df.columns)
            default_sort_index = sort_options.index('Size') if 'Size' in sort_options else 0
            sort_col = st.selectbox("Sort by", options=sort_options, index=default_sort_index)
            sort_order = st.radio("Order", options=["Ascending", "Descending"], horizontal=True, index=1)
```

(index=1 makes Descending the default.)

- [ ] **Step 3: Compile-check**

```bash
python3.14 -m py_compile ui/ui.py && echo OK
```
Expected: `OK`.

- [ ] **Step 4: Visual check with Playwright (seeded DB)**

Seed a small multi-listing DB (varied prices, sizes, and feature booleans), launch streamlit
headless, and confirm via `browser_snapshot` (NOT just HTTP 200 — an in-page exception returns 200):
no exception element; the table shows emoji in Gym/Pool/Parking/EV columns; rows are ordered by
Size descending by default. Take a screenshot.

Seed + launch pattern (reuse from prior UI tasks): write a `/tmp/seed_ui.py` creating `listings`,
`prices`, `listing_changes` with 3 rows at different sizes/prices and mixed feature booleans, copy
to `./rents.db`, run `streamlit run ui/ui.py --server.headless=true --server.port=8605`, drive with
Playwright, then `rm -f ./rents.db`. If Playwright MCP is unavailable, fall back to curl + grep the
served HTML for the emoji characters and absence of "StreamlitAPIException".

- [ ] **Step 5: Commit**

```bash
git add ui/ui.py
git commit -m "feat(ui) default sort to Size desc and render feature columns as emoji"
```

---

## Task 3: Market-over-time dashboard (Tab 3 restructure)

**Files:**
- Modify: `ui/ui.py`

**Interfaces:**
- Consumes: `craigscraper.market_analysis` (Task 1); `get_connection()`; `listings`/`prices` tables.
- Produces: no new consumers.

- [ ] **Step 1: Add cached, parameterized data helpers**

Near the other `@st.cache_data` helpers (top of `ui.py`), add:

```python
from craigscraper.market_analysis import (
    monthly_median_rent, new_listings_per_month, pct_change_vs, active_listings_per_month
)

@st.cache_data(ttl=300)
def load_price_months():
    # one row per price point: month, bedrooms, price (joined to the listing's bedroom count)
    conn = get_connection()
    query = """
    SELECT strftime('%Y-%m', p.last_updated) AS month, l.bedrooms AS bedrooms, p.price AS price
    FROM prices p
    JOIN listings l ON l.id = p.listing_id
    WHERE l.bedrooms IS NOT NULL AND p.price IS NOT NULL
    """
    return pd.read_sql_query(query, conn)

@st.cache_data(ttl=300)
def load_posted_and_dom():
    # posted month (for new-listings) and approximate days-on-market per listing
    conn = get_connection()
    query = """
    SELECT strftime('%Y-%m', posted_on) AS posted_month,
           strftime('%Y-%m', last_updated) AS last_month,
           posted_on, last_updated
    FROM listings
    WHERE posted_on IS NOT NULL
    """
    df = pd.read_sql_query(query, conn)
    df['posted_on'] = pd.to_datetime(df['posted_on'], errors='coerce', utc=True)
    df['last_updated'] = pd.to_datetime(df['last_updated'], errors='coerce', utc=True)
    df['days_on_market'] = (df['last_updated'] - df['posted_on']).dt.days
    return df
```

- [ ] **Step 2: Replace the Tab 3 body with three sub-tabs**

Replace the entire `with tab3:` block (from `st.markdown('<div class="subheader">Market Statistics</div>'...)`
through the end of the current stat_tab1/stat_tab2 content) with:

```python
    with tab3:
        st.markdown('<div class="subheader">Market Statistics</div>', unsafe_allow_html=True)
        rent_tab, activity_tab, snapshot_tab = st.tabs(["Rent over time", "Market activity", "Snapshot"])

        # ---- Rent over time ----
        with rent_tab:
            price_months = load_price_months()
            medians = monthly_median_rent(price_months)
            if medians.empty:
                st.info("Not enough price history yet to chart rent over time.")
            else:
                fig = px.line(
                    medians, x='month', y='median_price', color='bedrooms', markers=True,
                    title='Median asking rent by month (per bedroom count)',
                    labels={'month': 'Month', 'median_price': 'Median rent ($)', 'bedrooms': 'Bedrooms'}
                )
                fig.update_layout(yaxis=dict(tickprefix="$"), hovermode="x")
                st.plotly_chart(fig, width="stretch")

                # overall median per month (all bedrooms pooled) for the delta metrics
                overall = (price_months.groupby('month')['price'].median()
                           .reset_index().rename(columns={'price': 'median_price'})
                           .sort_values('month'))
                c3, c6, c12 = st.columns(3)
                for col, months_back, label in ((c3, 3, "vs 3 mo ago"),
                                                 (c6, 6, "vs 6 mo ago"),
                                                 (c12, 12, "vs 12 mo ago")):
                    pct = pct_change_vs(overall, months_back)
                    latest = overall.iloc[-1]['median_price']
                    with col:
                        st.metric(label, f"${latest:,.0f}",
                                  delta=(f"{pct:+.1f}%" if pct is not None else "n/a"))

        # ---- Market activity ----
        with activity_tab:
            pdom = load_posted_and_dom()
            if pdom.empty:
                st.info("Not enough listing data yet to chart market activity.")
            else:
                newpm = new_listings_per_month(pdom['posted_month'])
                fig = px.bar(newpm, x='month', y='count', title='New listings per month',
                             labels={'month': 'Month', 'count': 'New listings'})
                st.plotly_chart(fig, width="stretch")

                # inventory over time: active listings per month (posted_month..last_month inclusive)
                inv = active_listings_per_month(pdom[['posted_month', 'last_month']])
                if not inv.empty:
                    fig = px.line(inv, x='month', y='active', markers=True,
                                  title='Active listings per month (inventory, approximate)',
                                  labels={'month': 'Month', 'active': 'Active listings'})
                    st.plotly_chart(fig, width="stretch")
                    st.caption("Inventory is approximate: a listing is counted as active from its "
                               "posted month through its last-seen month.")

                # approximate days-on-market by posted month
                dom = (pdom.dropna(subset=['days_on_market'])
                       .groupby('posted_month')['days_on_market'].mean().reset_index())
                if not dom.empty:
                    fig = px.line(dom, x='posted_month', y='days_on_market', markers=True,
                                  title='Average days on market by posted month (approximate)',
                                  labels={'posted_month': 'Month', 'days_on_market': 'Avg days on market'})
                    st.plotly_chart(fig, width="stretch")
                    st.caption("Days on market is approximate: measured from posted date to the "
                               "listing's last-seen date (we detect removal at the next scrape).")

        # ---- Snapshot (existing charts, regrouped by bedrooms) ----
        with snapshot_tab:
            snap_price, snap_rented = st.tabs(["Price Distribution", "Rented Properties"])
            with snap_price:
                if not listings_df.empty:
                    fig = px.box(listings_with_trends, x='bedrooms', y='last_price',
                                 title='Price Distribution by Bedrooms',
                                 labels={'bedrooms': 'Bedrooms', 'last_price': 'Price ($)'})
                    st.plotly_chart(fig, width="stretch")

                    price_stats = listings_with_trends.groupby('bedrooms').agg(
                        avg_price=('last_price', 'mean'),
                        median_price=('last_price', 'median'),
                        min_price=('last_price', 'min'),
                        max_price=('last_price', 'max'),
                        count=('id', 'count')
                    ).reset_index()
                    for c in ['avg_price', 'median_price', 'min_price', 'max_price']:
                        price_stats[c] = price_stats[c].round().astype(int).apply(format_price)
                    price_stats.columns = ['Bedrooms', 'Average Price', 'Median Price', 'Min Price', 'Max Price', 'Count']
                    st.write(price_stats)

                    st.subheader("Price Trend Analysis")
                    trend_data = listings_with_trends.groupby('trend').size().reset_index(name='count')
                    if not trend_data.empty:
                        fig = px.pie(trend_data, values='count', names='trend',
                                     title='Price Trend Distribution', color='trend',
                                     color_discrete_map={'increase': 'red', 'decrease': 'green', 'stable': 'gray'})
                        st.plotly_chart(fig, width="stretch")
                else:
                    st.warning("Not enough data to display price distribution.")

            with snap_rented:
                rented_df = listings_df[listings_df['still_published'] == False].copy()
                if rented_df.empty:
                    st.info("No data available for rented properties yet.")
                else:
                    rented_stats = rented_df.groupby('bedrooms').agg(
                        avg_price=('last_price', 'mean'),
                        min_price=('last_price', 'min'),
                        max_price=('last_price', 'max'),
                        count=('id', 'count')
                    ).reset_index()
                    for c in ['avg_price', 'min_price', 'max_price']:
                        rented_stats[c] = rented_stats[c].round().astype(int).apply(format_price)
                    rented_stats.columns = ['Bedrooms', 'Average Price', 'Min Price', 'Max Price', 'Count']
                    st.table(rented_stats)

                    fig = px.histogram(rented_df, x='last_price', color='bedrooms',
                                       title='Distribution of Rented Property Prices',
                                       labels={'last_price': 'Price ($)'}, nbins=20)
                    st.plotly_chart(fig, width="stretch")
```

Note: this removes the old `rental_stats`-based code path (which grouped by `rooms`); `rented_stats`
is computed inline by `bedrooms`. If `get_rental_statistics` / `rental_stats` become unused after this,
leave the function defined (out of scope to delete) but it's fine if `rental_stats` var is no longer read.

- [ ] **Step 3: Confirm no remaining raw-`rooms` grouping in the market tab**

```bash
grep -n "groupby('rooms')\|x='rooms'\|color='rooms'" ui/ui.py
```
Expected: no output (all regrouped to `bedrooms`).

- [ ] **Step 4: Compile-check**

```bash
python3.14 -m py_compile ui/ui.py && echo OK
```
Expected: `OK`.

- [ ] **Step 5: Visual check with Playwright against a seeded multi-month DB**

Seed a DB whose `prices` span ≥3 months with ≥5 points per (month, 1BR) bucket and a couple of
bedroom counts, plus varied `posted_on`. Launch streamlit headless, open Tab 3 → click each of the
three sub-tabs (Rent over time, Market activity, Snapshot → both nested tabs). For EACH, take a
`browser_snapshot` and confirm NO `StreamlitAPIException`/exception element, and that charts render
(not an empty-data `st.info`). Screenshot the Rent-over-time sub-tab. `rm -f ./rents.db` after.

- [ ] **Step 6: Commit**

```bash
git add ui/ui.py
git commit -m "feat(ui) rebuild Market Statistics into rent-over-time + activity + snapshot dashboard"
```

---

## Task 4: End-to-end verification + deploy

**Files:** none modified — verify + deploy.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: the enhanced UI live on the host.

- [ ] **Step 1: Run the full unit suite**

```bash
/tmp/pytestenv/bin/python -m pytest tests/ -q
```
Expected: all tests pass (parse_rooms + market_analysis).

- [ ] **Step 2: Playwright check against LIVE prod data before deploy is not possible (prod not yet updated); instead verify against a copy of the prod DB**

If a prod DB copy is available locally, run the UI against it and click through Tab 1 (emoji + Size-desc)
and Tab 3 (all sub-tabs) confirming no exceptions and populated charts on real-shaped data. Otherwise
rely on the seeded checks from Tasks 2-3. Record which was done.

- [ ] **Step 3: Push and build**

```bash
git push origin main
sleep 8
RID=$(gh run list --repo porelli/craigscraper --workflow "Create and publish a Docker image" --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RID" --repo porelli/craigscraper --exit-status
gh run view "$RID" --repo porelli/craigscraper --json status,conclusion
```
Expected: `conclusion: success`.

- [ ] **Step 4: Back up prod DB and recreate the container**

```bash
ssh hpmini600g2 'sudo -n cp -v /datablind/containers-volumes/craigscraper/rents.db /datablind/containers-volumes/craigscraper/rents.db.bak-marketui'
ssh hpmini600g2 'docker pull ghcr.io/porelli/craigscraper:main | tail -1
docker run --rm ghcr.io/porelli/craigscraper:main python3 --version
docker stop craigscraper >/dev/null 2>&1 && docker rm craigscraper >/dev/null 2>&1
docker run -d --name craigscraper --restart unless-stopped -p 2352:8501 \
  -v /datablind/containers-volumes/craigscraper:/persist \
  -e MIN_PRICE=2000 -e MAX_PRICE=4000 -e LAT=49.2822 -e LON=-123.1284 \
  -e MIN_BEDROOMS=1 -e SEARCH_DISTANCE=1.41 \
  -e DISTANCE_FROM_LAT=49.2799016 -e DISTANCE_FROM_LON=-123.1167676 \
  -e RENTS_DB=/persist/rents.db -e SUPPRESS_TEST_NOTIFICATION=True \
  ghcr.io/porelli/craigscraper:main
sleep 3; docker ps --filter name=craigscraper --format "{{.Names}} {{.Status}}"'
```
Expected: `Python 3.14.x`; container `Up`.

- [ ] **Step 5: Verify live prod UI with Playwright**

Navigate to `http://hpmini600g2:2352/`, wait for "Available Properties". Confirm Tab 1 shows emoji
feature columns and Size-descending order. Open Tab 3, click all three sub-tabs, and for each confirm
via `browser_snapshot` there is NO exception element and charts populate against the real 18-month data.
Screenshot the Rent-over-time chart. Also verify no error in logs:
```bash
ssh hpmini600g2 'docker logs craigscraper 2>&1 | grep -icE "Traceback|StreamlitAPIException|OperationalError"'
```
Expected: 0 errors; charts render on real data.

- [ ] **Step 6: Clean up local artifacts**

```bash
cd /Volumes/workspace/craigscraper && rm -f ./rents.db *.png && rm -rf .playwright-mcp
git status --short
```
Expected: clean tree.

---

## Self-Review Notes

- **Spec coverage:** Size-desc default (Task 2 Step 2) ✓; emoji columns (Task 2 Step 1) ✓; rent-over-time with per-bedroom medians + 3/6/12mo deltas + sparsity guard (Task 1 + Task 3 Step 2) ✓; market activity: new listings/month + approximate labeled days-on-market (Task 3 Step 2) ✓; snapshot regrouped by bedrooms (Task 3 Step 2) ✓; parameterized SQL (Task 3 Step 1) ✓; median in pandas not SQLite (Task 1) ✓; unit test aggregation (Task 1) ✓; Playwright visual verification incl. exception-element check (Tasks 2,3,4) ✓; deploy with backup, no migration (Task 4) ✓; caveats out of scope (numeric sort, true removal date) — noted, not built ✓.
- **Placeholder scan:** none — all code and commands concrete.
- **Type/name consistency:** `monthly_median_rent`/`new_listings_per_month`/`pct_change_vs`/`MIN_POINTS_PER_BUCKET` defined in Task 1 and imported/called identically in Task 3; helper columns (`month`, `bedrooms`, `price`, `median_price`, `n`, `count`, `posted_month`, `days_on_market`) consistent between the SQL helpers and the aggregation functions.
- **Inventory-over-time:** included per user decision — `active_listings_per_month` (Task 1) expands each listing across posted_month..last_month inclusive, unit-tested (inclusive span + year boundary), and charted in the activity tab (Task 3) labeled approximate.
```

# Listing Change-History + Rooms Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the opaque `rooms` text into structured columns, record a per-listing change history for content fields (forward-only), and surface a per-listing price+field timeline in a UI modal opened from the price-trend arrow.

**Architecture:** A shared `parse_rooms()` helper feeds both the crawler and an idempotent backfill. The pipeline gains three `listings` columns and a new `listing_changes` table, and diffs stored-vs-incoming content fields before its `INSERT OR REPLACE`. The Streamlit UI turns the price arrow into a `?history=<id>` anchor that opens an `@st.dialog` timeline merging `prices` and `listing_changes`.

**Tech Stack:** Python 3.14, Scrapy 2.17, SQLite, pandas 3.0, Streamlit 1.59 (native `@st.dialog`, `st.query_params`), pytest (new — first tests in this repo).

## Global Constraints

- Python 3.14; run local commands with `python3.14` (system `python3` is 3.9 and cannot parse `match`).
- Dependencies are locked: if a new dev/test dep (pytest) is needed, add it to `requirements.in` and regenerate `requirements.txt` **inside a Linux `python:3.14` container** (`docker run --rm -v "$PWD":/app -w /app python:3.14 sh -c "pip install -q --upgrade pip pip-tools && pip-compile --generate-hashes --upgrade --output-file requirements.txt requirements.in"`), never on macOS (platform-gated deps like watchdog get dropped). Install with `--require-hashes`.
- Rooms columns: `bedrooms REAL`, `bathrooms REAL`, `bathrooms_type TEXT`; raw `rooms TEXT` is KEPT unchanged. No `bedrooms_type` column (bedrooms are always integers in the data).
- `parse_rooms` must never raise; malformed input → all three values None.
- Change-history tracked fields (content only): `title`, `description`, `attributes`, `available_on`, `size`, `rooms`. Excluded: `price` (own table), `distance`, `still_published`, `last_updated`, `posted_on`, and the derived room columns.
- `attributes` is compared/stored as its `', '.join(...)` string form.
- `changed_at` uses `item['last_updated']` (deterministic/testable), not wall-clock now().
- Change-history is FORWARD-ONLY: no backfill, no synthetic baseline. Rooms IS backfilled for all existing rows.
- Modal uses `st.query_params` (NOT the removed `experimental_*` APIs) + `@st.dialog`. The existing HTML table in Tab 1 stays; only the price-trend cell becomes an anchor.
- Timeline dedupes CONSECUTIVE identical prices for DISPLAY only — never modifies stored `prices` rows.
- Deploy via the proven flow (see repo memory `deployment`): back up `rents.db` with `sudo cp`, push to main, CI build, pull-by-digest (verify python version), recreate container, verify logs + UI HTTP 200.

---

## File Structure

- **`craigscraper/spiders/shared_utils.py`** — MODIFY. Add `parse_rooms(rooms_str)` returning `{'bedrooms','bathrooms','bathrooms_type'}`.
- **`tests/test_parse_rooms.py`** — CREATE. First unit tests in the repo (pytest).
- **`craigscraper/spiders/rent.py`** — MODIFY. In `parseItem`, set the three room fields from `parse_rooms(item['rooms'])`.
- **`craigscraper/pipelines.py`** — MODIFY. Add 3 columns to `listing_columns` + `INSERT OR REPLACE`; add `listing_changes` table + index; add change-diff logic in `process_item`; add a rooms backfill routine.
- **`ui/ui.py`** — MODIFY. Price arrow → `?history=<id>` anchor; `@st.dialog` timeline; query-param handling in `main()`; timeline query helpers.
- **`requirements.in` / `requirements.txt`** — MODIFY only if pytest is added as a managed dep (Task 1 decides).

---

## Task 1: Add `parse_rooms` helper + unit tests

**Files:**
- Modify: `craigscraper/spiders/shared_utils.py`
- Create: `tests/test_parse_rooms.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SharedUtils.parse_rooms(rooms_str) -> dict` with keys `bedrooms: float|None`, `bathrooms: float|None`, `bathrooms_type: str|None`. Used by `rent.py` (Task 3) and the pipeline backfill (Task 2).

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_rooms.py`. pytest is available via a throwaway venv; do NOT add pytest to requirements.in (it's a dev-only tool, and the lockfile is runtime-only — mirror how pip-tools is handled). Run tests with a dedicated venv (see Step 2).

```python
from craigscraper.spiders.shared_utils import SharedUtils

u = SharedUtils()

def test_simple_integer_bath():
    assert u.parse_rooms('1BR / 1Ba') == {'bedrooms': 1.0, 'bathrooms': 1.0, 'bathrooms_type': None}

def test_half_bath():
    assert u.parse_rooms('2BR / 1.5Ba') == {'bedrooms': 2.0, 'bathrooms': 1.5, 'bathrooms_type': None}

def test_split_bath():
    assert u.parse_rooms('1BR / splitBa') == {'bedrooms': 1.0, 'bathrooms': None, 'bathrooms_type': 'split'}

def test_shared_bath():
    assert u.parse_rooms('1BR / sharedBa') == {'bedrooms': 1.0, 'bathrooms': None, 'bathrooms_type': 'shared'}

def test_three_bed_two_bath():
    assert u.parse_rooms('3BR / 2Ba') == {'bedrooms': 3.0, 'bathrooms': 2.0, 'bathrooms_type': None}

def test_malformed_returns_all_none():
    assert u.parse_rooms('garbage') == {'bedrooms': None, 'bathrooms': None, 'bathrooms_type': None}

def test_none_input():
    assert u.parse_rooms(None) == {'bedrooms': None, 'bathrooms': None, 'bathrooms_type': None}
```

- [ ] **Step 2: Run the test, verify it fails**

```bash
rm -rf /tmp/pytestenv && python3.14 -m venv /tmp/pytestenv && /tmp/pytestenv/bin/pip install -q pytest
/tmp/pytestenv/bin/python -m pytest tests/test_parse_rooms.py -v
```
Expected: FAIL — `AttributeError: 'SharedUtils' object has no attribute 'parse_rooms'`.

- [ ] **Step 3: Implement `parse_rooms`**

Add to `craigscraper/spiders/shared_utils.py` inside the `SharedUtils` class:

```python
    def parse_rooms(self, rooms_str):
        # Split e.g. '2BR / 1.5Ba' into structured fields. Bedrooms are always integers;
        # bathrooms are numeric (incl. halves) or a text type ('split'/'shared'). Never raises.
        result = {'bedrooms': None, 'bathrooms': None, 'bathrooms_type': None}
        if not rooms_str or '/' not in rooms_str:
            return result

        bed_part, bath_part = rooms_str.split('/', 1)

        bed_digits = ''.join(ch for ch in bed_part if ch.isdigit())
        if bed_digits:
            result['bedrooms'] = float(bed_digits)

        bath_token = bath_part.strip().removesuffix('Ba').removesuffix('ba').strip()
        try:
            result['bathrooms'] = float(bath_token)
        except ValueError:
            if bath_token:
                result['bathrooms_type'] = bath_token.lower()

        return result
```

- [ ] **Step 4: Run the test, verify it passes**

```bash
/tmp/pytestenv/bin/python -m pytest tests/test_parse_rooms.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add craigscraper/spiders/shared_utils.py tests/test_parse_rooms.py
git commit -m "feat(rooms) add parse_rooms helper with unit tests"
```

---

## Task 2: Pipeline — rooms columns, backfill, listing_changes table + diffing

**Files:**
- Modify: `craigscraper/pipelines.py`

**Interfaces:**
- Consumes: `SharedUtils.parse_rooms` (Task 1); `item['bedrooms']/['bathrooms']/['bathrooms_type']` produced by Task 3 (this task tolerates their absence via `item.get`, so ordering is safe either way).
- Produces: `listings.bedrooms/bathrooms/bathrooms_type` columns; `listing_changes` table; change rows on content-field edits.

- [ ] **Step 1: Add the three rooms columns to `listing_columns`**

In `pipelines.py`, in `__init__`, change the `listing_columns` list to include the new columns (place after `"rooms TEXT",`):

```python
        listing_columns = [
            "id INTEGER PRIMARY KEY",
            "link TEXT",
            "rooms TEXT",
            "bedrooms REAL",
            "bathrooms REAL",
            "bathrooms_type TEXT",
            "available_on TEXT",
            "size INTEGER",
            "attributes BLOB",
            "description TEXT",
            "title TEXT",
            "gym TEXT",
            "pool TEXT",
            "parking TEXT",
            "ev_charging TEXT",
            "distance REAL",
            "last_price INTEGER",
            "last_updated TEXT",
            "posted_on TEXT",
            "still_published TEXT"
        ]
```
(The existing `ALTER TABLE` auto-add loop in `create_table_if_not_exists` will add these to the live DB on startup.)

- [ ] **Step 2: Create the `listing_changes` table and its index**

In `__init__`, after the two `create_table_if_not_exists` calls, add a direct create (it has no reprocessable columns, so it doesn't need that helper):

```python
        self.create_table_if_not_exists('listings', listing_columns)
        self.create_table_if_not_exists('prices', prices_columns, prices_constraints)

        # history of content-field edits (forward-only; populated in process_item)
        self.cur.execute("""CREATE TABLE IF NOT EXISTS listing_changes (
            listing_id INTEGER,
            field      TEXT,
            old_value  TEXT,
            new_value  TEXT,
            changed_at TEXT
        )""")
        self.cur.execute("""CREATE INDEX IF NOT EXISTS listing_changes_ids ON listing_changes(listing_id)""")
        self.con.commit()
```

- [ ] **Step 3: Add a rooms backfill routine and call it**

The existing `backfill_null_column` handles string features via `findFeature`; rooms needs its own routine (parses one source into three columns). Add this method to the class:

```python
    def backfill_rooms(self):
        utils = SharedUtils()
        self.con.row_factory = sqlite3.Row
        special_cur = self.con.cursor()
        self.con.row_factory = None

        special_cur.execute("SELECT id, rooms FROM listings WHERE bedrooms IS NULL AND rooms IS NOT NULL")
        rows = special_cur.fetchall()
        if not rows:
            return

        print(colored(f"Backfilling parsed rooms for {len(rows)} row(s)...", 'cyan'))
        updated = 0
        for row in rows:
            parsed = utils.parse_rooms(row['rooms'])
            self.con.execute(
                "UPDATE listings SET bedrooms = ?, bathrooms = ?, bathrooms_type = ? WHERE id = ?",
                (parsed['bedrooms'], parsed['bathrooms'], parsed['bathrooms_type'], row['id'])
            )
            updated += 1

        self.con.commit()
        print(colored(f"Rooms backfill complete. Updated rows: {updated}", 'green'))
```

Call it in `__init__` after the table setup (after the `listing_changes` block):

```python
        self.backfill_rooms()
```

- [ ] **Step 4: Add the three columns to the `INSERT OR REPLACE`**

In `process_item`, update the listings insert. Column list (add `bedrooms, bathrooms, bathrooms_type` after `rooms`):

```python
        self.cur.execute("""INSERT or REPLACE into listings
                            (id, link, rooms, bedrooms, bathrooms, bathrooms_type, available_on, size, attributes, description, title, gym, pool, parking, ev_charging, distance, last_price, last_updated, posted_on, still_published) VALUES
                            (?,  ?,    ?,     ?,        ?,         ?,              ?,            ?,    ?,          ?,           ?,     ?,   ?,    ?,       ?,           ?,        ?,          ?,            ?,         ?)""",
                         (
                             item['id'],
                             item['link'],
                             item['rooms'],
                             item.get('bedrooms'),
                             item.get('bathrooms'),
                             item.get('bathrooms_type'),
                             item['available_on'],
                             item['size'],
                             ', '.join(item['attributes']),
                             item['description'],
                             item['title'],
                             item['gym'],
                             item['pool'],
                             item['parking'],
                             item['ev_charging'],
                             item['distance'],
                             item['price'],
                             item['last_updated'],
                             item['posted_on'],
                             'True' # always set still_published as true during insert
                         )
        )
```

- [ ] **Step 5: Add change-diffing BEFORE the insert**

At the very start of `process_item` (before the `INSERT OR REPLACE` above), add:

```python
        # record content-field edits before we overwrite the stored row (forward-only history)
        tracked_fields = ['title', 'description', 'attributes', 'available_on', 'size', 'rooms']
        incoming = {
            'title': item['title'],
            'description': item['description'],
            'attributes': ', '.join(item['attributes']),  # stored form
            'available_on': item['available_on'],
            'size': item['size'],
            'rooms': item['rooms'],
        }
        self.cur.execute(
            "SELECT title, description, attributes, available_on, size, rooms FROM listings WHERE id = ?",
            (item['id'],)
        )
        stored = self.cur.fetchone()
        if stored is not None:
            stored_map = dict(zip(tracked_fields, stored))
            for field in tracked_fields:
                old_val = stored_map[field]
                new_val = incoming[field]
                # normalize to string for a stable comparison (DB returns native types)
                if (old_val if old_val is None else str(old_val)) != (new_val if new_val is None else str(new_val)):
                    self.cur.execute(
                        "INSERT INTO listing_changes (listing_id, field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?)",
                        (item['id'], field, None if old_val is None else str(old_val),
                         None if new_val is None else str(new_val), item['last_updated'])
                    )
```

- [ ] **Step 6: Compile-check**

```bash
python3.14 -m py_compile craigscraper/pipelines.py && echo OK
```
Expected: `OK`.

- [ ] **Step 7: Integration test against a throwaway DB**

Verify columns, table, backfill, and diffing end-to-end with a small script:

```bash
cat > /tmp/test_pipeline.py <<'PY'
import sqlite3, os, sys
sys.path.insert(0, os.getcwd())
os.environ['RENTS_DB'] = '/tmp/tp.db'
if os.path.exists('/tmp/tp.db'): os.remove('/tmp/tp.db')

# seed a legacy row (no parsed rooms) to prove backfill
c = sqlite3.connect('/tmp/tp.db'); cur = c.cursor()
cur.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, link TEXT, rooms TEXT, available_on TEXT, size INTEGER, attributes BLOB, description TEXT, title TEXT, gym TEXT, pool TEXT, parking TEXT, ev_charging TEXT, distance REAL, last_price INTEGER, last_updated TEXT, posted_on TEXT, still_published TEXT)")
cur.execute("INSERT INTO listings (id, rooms, title, description, attributes, available_on, size) VALUES (1, '2BR / 1.5Ba', 'Old title', 'old desc', 'a, b', '2025-01-01', 700)")
c.commit(); c.close()

from craigscraper.pipelines import CraigscraperPipeline
p = CraigscraperPipeline()  # __init__ runs migrations + rooms backfill

# assert backfill populated parsed rooms for the legacy row
cur = p.con.cursor()
cur.execute("SELECT bedrooms, bathrooms, bathrooms_type FROM listings WHERE id=1")
print("backfilled rooms:", cur.fetchone())  # expect (2.0, 1.5, None)

# process an updated version of listing 1 (title + rooms changed) -> should log 2 changes
class S: first_run = True
item = {'id':1,'link':'x','rooms':'2BR / 2Ba','available_on':'2025-01-01','size':700,
        'attributes':['a','b'],'description':'old desc','title':'New title',
        'gym':'False','pool':'False','parking':'False','ev_charging':'False',
        'distance':1.0,'price':2000,'last_updated':'2025-02-01','posted_on':'2025-01-01',
        'bedrooms':2.0,'bathrooms':2.0,'bathrooms_type':None}
p.process_item(item, S())
cur.execute("SELECT field, old_value, new_value FROM listing_changes WHERE listing_id=1 ORDER BY field")
print("changes:", cur.fetchall())  # expect rooms and title changes
PY
/tmp/pytestenv/bin/python /tmp/test_pipeline.py
```
Expected: `backfilled rooms: (2.0, 1.5, None)` and `changes:` containing a `rooms` (1.5Ba→2Ba) and `title` (Old title→New title) entry, and NO description/size/attributes/available_on entries.

- [ ] **Step 8: Commit**

```bash
git add craigscraper/pipelines.py
git commit -m "feat(sql) add parsed rooms columns, rooms backfill, and listing_changes history"
```

---

## Task 3: Crawler — populate parsed rooms in `parseItem`

**Files:**
- Modify: `craigscraper/spiders/rent.py`

**Interfaces:**
- Consumes: `SharedUtils.parse_rooms` (Task 1), via the spider's existing `self.utils`.
- Produces: `item['bedrooms']`, `item['bathrooms']`, `item['bathrooms_type']` consumed by the pipeline insert (Task 2, Step 4).

- [ ] **Step 1: Set the parsed room fields after `item['rooms']` is finalized**

In `rent.py:parseItem`, `item['rooms']` is set inside the attribute loop (assigned from the `.*BR.*` case). AFTER that loop completes (so `item['rooms']` holds its final value), and before `yield item`, add:

```python
        parsed_rooms = self.utils.parse_rooms(item['rooms'])
        item['bedrooms'] = parsed_rooms['bedrooms']
        item['bathrooms'] = parsed_rooms['bathrooms']
        item['bathrooms_type'] = parsed_rooms['bathrooms_type']
```

Place this immediately before `yield item` at the end of `parseItem`. (`item['rooms']` may be None if no BR attribute matched; `parse_rooms(None)` safely returns all-None.)

- [ ] **Step 2: Compile-check**

```bash
python3.14 -m py_compile craigscraper/spiders/rent.py && echo OK
```
Expected: `OK`.

- [ ] **Step 3: Live bounded crawl — confirm parsed rooms land in the DB**

```bash
rm -rf /tmp/appverify && python3.14 -m venv /tmp/appverify && /tmp/appverify/bin/pip install -q --require-hashes -r requirements.txt
rm -f /tmp/rooms_rents.db
env RENTS_DB=/tmp/rooms_rents.db SUPPRESS_TEST_NOTIFICATION=True \
  /tmp/appverify/bin/scrapy crawl rent -s CLOSESPIDER_ITEMCOUNT=3 -s LOG_LEVEL=WARNING
/tmp/appverify/bin/python -c "
import sqlite3; c=sqlite3.connect('/tmp/rooms_rents.db'); cur=c.cursor()
cur.execute('SELECT rooms, bedrooms, bathrooms, bathrooms_type FROM listings LIMIT 5')
for r in cur.fetchall(): print(r)
cur.execute('SELECT count(*), sum(bedrooms IS NULL) FROM listings'); print('rows / null bedrooms:', cur.fetchone())
"
```
Expected: rows show `rooms` text alongside populated `bedrooms`/`bathrooms`; null bedrooms count is 0 (every real Vancouver listing has a BR/Ba attribute).

- [ ] **Step 4: Commit**

```bash
git add craigscraper/spiders/rent.py
git commit -m "feat(item) populate parsed bedrooms/bathrooms fields from rooms"
```

---

## Task 4: UI — timeline modal on the price arrow

**Files:**
- Modify: `ui/ui.py`

**Interfaces:**
- Consumes: `listing_changes` + `prices` tables; `get_connection()` (existing).
- Produces: a `?history=<id>` anchor in the price-trend cell and an `@st.dialog` timeline. No new consumers.

- [ ] **Step 1: Add a timeline data helper**

Add near the other data helpers (after `get_property_price_history`, ~line 137). It merges price events (consecutive-dedup) and field changes:

```python
@st.cache_data(ttl=300)
def get_listing_timeline(listing_id):
    conn = get_connection()
    events = []

    # price events: chronological, collapse consecutive identical prices (display-only)
    prices = pd.read_sql_query(
        "SELECT last_updated, price FROM prices WHERE listing_id = ? ORDER BY last_updated",
        conn, params=(listing_id,)
    )
    prev = None
    for i, r in prices.iterrows():
        if prev is None:
            events.append({'when': r['last_updated'], 'field': 'price', 'old': '', 'new': f"${int(r['price']):,}"})
        elif r['price'] != prev:
            events.append({'when': r['last_updated'], 'field': 'price', 'old': f"${int(prev):,}", 'new': f"${int(r['price']):,}"})
        prev = r['price']

    # field changes
    changes = pd.read_sql_query(
        "SELECT changed_at, field, old_value, new_value FROM listing_changes WHERE listing_id = ? ORDER BY changed_at",
        conn, params=(listing_id,)
    )
    for _, r in changes.iterrows():
        events.append({'when': r['changed_at'], 'field': r['field'], 'old': r['old_value'], 'new': r['new_value']})

    df = pd.DataFrame(events)
    if not df.empty:
        df['when'] = pd.to_datetime(df['when'], errors='coerce', utc=True)
        df = df.sort_values(by='when', ascending=False)
    return df
```

Note the SQL uses parameterized queries (`params=`), not f-strings.

- [ ] **Step 2: Add the `@st.dialog` modal function**

Add above `main()`:

```python
@st.dialog("Listing timeline")
def show_timeline_dialog(listing_id, title):
    st.write(f"**{title}**")
    df = get_listing_timeline(listing_id)
    if df.empty:
        st.info("No recorded changes yet.")
        return
    disp = df.copy()
    disp['when'] = disp['when'].dt.strftime('%Y-%m-%d')
    disp['change'] = disp['old'].fillna('').astype(str) + ' → ' + disp['new'].fillna('').astype(str)
    st.dataframe(
        disp[['when', 'field', 'change']].rename(columns={'when': 'When', 'field': 'What', 'change': 'Change'}),
        width="stretch", hide_index=True
    )
```

- [ ] **Step 3: Make the price-trend cell a link to `?history=<id>`**

In `create_price_trend` (the `else` branch that builds `clean_html`), wrap the output in an anchor to the query param. Replace the `clean_html` assignment block:

```python
            property_id = row.get('id')

            # Link the trend to open the timeline modal via query param
            clean_html = (
                f'<a href="?history={property_id}" target="_self" '
                f'style="color: {color}; text-decoration: none; font-weight: bold;">'
                f'{trend_symbol} {formatted_pct}</a> {price_changes}'
            )
            return clean_html
```

Also wrap the single-price early-return (the `if history.empty or len(history) <= 1:` branch) so it's clickable too:

```python
        if history.empty or len(history) <= 1:
            # No history or only one price point
            return (f'<a href="?history={row.get("id")}" target="_self" '
                    f'style="color: {color}; text-decoration: none;">{trend_symbol} {formatted_pct}</a>')
```

- [ ] **Step 4: Handle the query param in `main()`**

At the very start of `main()` (after the header markdown, before/after data load is fine, but it needs `listings_df`), add handling. Put it right after `listings_df`/`prices_df` are loaded and the empty-check passes:

```python
    # open the timeline modal if navigated via ?history=<id>
    hist_param = st.query_params.get("history")
    if hist_param:
        try:
            hist_id = int(hist_param)
            match = listings_df[listings_df['id'] == hist_id]
            title = match.iloc[0]['title'] if not match.empty else str(hist_id)
            show_timeline_dialog(hist_id, title)
        except (ValueError, TypeError):
            pass
        finally:
            st.query_params.clear()
```

Place it immediately after the `else: st.error(...); return` block (so `listings_df` is known non-empty).

- [ ] **Step 5: Compile-check**

```bash
python3.14 -m py_compile ui/ui.py && echo OK
```
Expected: `OK`.

- [ ] **Step 6: Manual UI smoke test with a seeded timeline**

Seed a DB with a listing that has price + field history, launch the UI, and verify the modal.

```bash
cat > /tmp/seed_ui.py <<'PY'
import sqlite3
c = sqlite3.connect('/Volumes/workspace/craigscraper/rents.db'); cur = c.cursor()
cur.executescript("""
DROP TABLE IF EXISTS listings; DROP TABLE IF EXISTS prices; DROP TABLE IF EXISTS listing_changes;
CREATE TABLE listings (id INTEGER PRIMARY KEY, link TEXT, rooms TEXT, bedrooms REAL, bathrooms REAL, bathrooms_type TEXT, available_on TEXT, size INTEGER, attributes BLOB, description TEXT, title TEXT, gym TEXT, pool TEXT, parking TEXT, ev_charging TEXT, distance REAL, last_price INTEGER, last_updated TEXT, posted_on TEXT, still_published TEXT);
CREATE TABLE prices (listing_id INTEGER, last_updated TEXT, price INTEGER);
CREATE TABLE listing_changes (listing_id INTEGER, field TEXT, old_value TEXT, new_value TEXT, changed_at TEXT);
INSERT INTO listings VALUES (1,'http://x','2BR / 1Ba',2,1,NULL,'2025-02-01',700,'a, b','desc','Nice 2BR','False','False','True','False',1.2,2100,'2025-02-10','2025-01-01','True');
INSERT INTO prices VALUES (1,'2025-01-01',2000),(1,'2025-01-15',2000),(1,'2025-02-01',2100);
INSERT INTO listing_changes VALUES (1,'title','Old 2BR','Nice 2BR','2025-02-01'),(1,'description','old','desc','2025-02-01');
""")
c.commit(); print("seeded")
PY
/tmp/appverify/bin/python /tmp/seed_ui.py
cd /Volumes/workspace/craigscraper
/tmp/appverify/bin/streamlit run ui/ui.py --server.headless=true --server.port=8599 > /tmp/ui_hist.log 2>&1 &
UI_PID=$!; sleep 12
echo "--- base page ---"; curl -s -o /dev/null -w "HTTP %{http_code}\n" "http://localhost:8599/"
echo "--- with history param ---"; curl -s -o /dev/null -w "HTTP %{http_code}\n" "http://localhost:8599/?history=1"
grep -iE "Traceback|Exception" /tmp/ui_hist.log | grep -viE "error creating price trend" | head
kill $UI_PID 2>/dev/null || true
rm -f /Volumes/workspace/craigscraper/rents.db
```
Expected: both HTTP 200, no genuine Traceback/Exception in the log. (If Playwright MCP is available, additionally load `/?history=1`, snapshot, and confirm a dialog with a 3-row timeline: title change, description change, and price 2000→2100 — note the two identical 2000 price rows collapse to one "listed at" entry.)

- [ ] **Step 7: Commit**

```bash
git add ui/ui.py
git commit -m "feat(ui) timeline modal on price arrow showing price and field-change history"
```

---

## Task 4b: UI — surface bedrooms/bathrooms columns + numeric filters

**Files:**
- Modify: `ui/ui.py`

**Interfaces:**
- Consumes: `listings.bedrooms`, `listings.bathrooms`, `listings.bathrooms_type` (populated by Task 2/3; `load_listings_data` already does `SELECT *` so they're in the DataFrame).
- Produces: no new consumers.

**Why:** The DB now has parsed room columns, but the UI still shows and filters the raw `rooms` text. This task surfaces the split: separate Bedrooms/Bathrooms table columns and numeric range filters replacing the raw-rooms multiselect. Also fixes a crash Playwright surfaced: the price slider raises `StreamlitAPIException` when `min_price == max_price` (only one price after filtering) — this bites in production when a narrow filter yields a single listing.

- [ ] **Step 1: Fix the price slider min==max crash**

Replace the price-range slider block (currently ~lines 313-320) so it degrades gracefully when min==max:

```python
    # Price range filter
    min_price = int(listings_df['last_price'].min()) if not listings_df.empty else 0
    max_price = int(listings_df['last_price'].max()) if not listings_df.empty else 5000
    if min_price >= max_price:
        # a slider requires min < max; with a single distinct price there's nothing to range over
        st.sidebar.write(f"Price: ${min_price:,}")
        price_range = (min_price, max_price)
    else:
        price_range = st.sidebar.slider(
            "Price Range",
            min_price,
            max_price,
            (min_price, max_price)
        )
```

- [ ] **Step 2: Replace the rooms multiselect with numeric bedroom/bathroom filters**

Replace the "Room filter" block (currently ~lines 322-329, the `available_rooms`/`selected_rooms` multiselect):

```python
    # Bedroom / bathroom filters (numeric, from the parsed columns)
    bed_vals = sorted(v for v in listings_df['bedrooms'].dropna().unique())
    bath_vals = sorted(v for v in listings_df['bathrooms'].dropna().unique())
    selected_beds = st.sidebar.multiselect(
        "Bedrooms",
        options=bed_vals,
        default=bed_vals,
        format_func=lambda x: f"{int(x)}" if float(x).is_integer() else f"{x}"
    )
    selected_baths = st.sidebar.multiselect(
        "Bathrooms",
        options=bath_vals,
        default=bath_vals,
        format_func=lambda x: f"{int(x)}" if float(x).is_integer() else f"{x}"
    )
```

- [ ] **Step 3: Update the filter application**

Replace the `selected_rooms` application (currently ~lines 351-352):

```python
    if selected_beds:
        filtered_df = filtered_df[filtered_df['bedrooms'].isin(selected_beds)]
    if selected_baths:
        filtered_df = filtered_df[filtered_df['bathrooms'].isin(selected_baths)]
```

(This filters on bathrooms numerically; rows with `bathrooms_type` set — split/shared — have NULL bathrooms and are handled by keeping them when the bathrooms filter is at its default full selection. Since NaN is never `.isin(...)`, note in a comment that split/shared bathrooms are excluded when the bathrooms filter is narrowed; acceptable given they're <0.1% of data.)

- [ ] **Step 4: Show Bedrooms/Bathrooms columns in the table**

In the `columns_to_display` list (~line 396) replace `'rooms'` with `'bedrooms', 'bathrooms'`:

```python
            columns_to_display = [
                'clickable_title', 'bedrooms', 'bathrooms', 'size', 'last_price', 'price_trend',
                'available_on', 'posted_on', 'distance', 'gym', 'pool', 'parking', 'ev_charging'
            ]
```

And in the `.rename(...)` map, replace the `'rooms': 'Rooms',` entry with:

```python
                'bedrooms': 'Bedrooms',
                'bathrooms': 'Bathrooms',
```

- [ ] **Step 5: Compile-check**

```bash
python3.14 -m py_compile ui/ui.py && echo OK
```
Expected: `OK`.

- [ ] **Step 6: Visual verification with Playwright (seeded multi-listing DB)**

Seed 3 listings with varied prices AND a single-price scenario check, launch the UI, and drive it:
- Confirm the table shows separate **Bedrooms** and **Bathrooms** columns (not the raw `1BR / 1Ba`).
- Confirm the sidebar shows **Bedrooms** and **Bathrooms** numeric filters (not "Number of Rooms").
- Confirm no `StreamlitAPIException` renders (check via `browser_snapshot` for an `alert`/`StreamlitAPIException`, not just HTTP 200 — the slider bug proved HTTP 200 can hide an in-page exception).
- Take a screenshot and confirm visually.

Use the seed + launch + Playwright navigate/snapshot/screenshot pattern from Task 4 Step 6. If Playwright MCP is unavailable, fall back to `browser`-free curl + grep the rendered HTML for "StreamlitAPIException" and for the "Bedrooms" column header.

- [ ] **Step 7: Commit**

```bash
git add ui/ui.py
git commit -m "feat(ui) show bedrooms/bathrooms columns and numeric filters; fix price slider min==max crash"
```

---

## Task 5: End-to-end verification (pre-deploy gate)

**Files:** none modified — verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: evidence that the full crawl → DB → UI path works on the upgraded stack. Deploy gate.

- [ ] **Step 1: Run the unit tests**

```bash
/tmp/pytestenv/bin/python -m pytest tests/ -v
```
Expected: all `test_parse_rooms` tests pass.

- [ ] **Step 2: Full-ish live crawl into a throwaway DB**

```bash
rm -f /tmp/e2e_rents.db
env RENTS_DB=/tmp/e2e_rents.db SUPPRESS_TEST_NOTIFICATION=True \
  /tmp/appverify/bin/scrapy crawl rent -s CLOSESPIDER_ITEMCOUNT=5 -s LOG_LEVEL=WARNING 2>&1 | tail -5
/tmp/appverify/bin/python -c "
import sqlite3; c=sqlite3.connect('/tmp/e2e_rents.db'); cur=c.cursor()
cur.execute('SELECT count(*), sum(bedrooms IS NULL) FROM listings'); print('rows / null bedrooms:', cur.fetchone())
cur.execute('SELECT name FROM sqlite_master WHERE type=\"table\"'); print('tables:', [r[0] for r in cur.fetchall()])
"
```
Expected: rows > 0, null bedrooms = 0, tables include `listings`, `prices`, `listing_changes`.

- [ ] **Step 3: Confirm change-diffing fires on re-crawl**

Run the crawl a second time against the SAME DB (nothing changed on CL in seconds, so expect ~0 new changes — this confirms no false positives), then confirm the table exists and didn't get spurious rows:

```bash
env RENTS_DB=/tmp/e2e_rents.db SUPPRESS_TEST_NOTIFICATION=True \
  /tmp/appverify/bin/scrapy crawl rent -s CLOSESPIDER_ITEMCOUNT=5 -s LOG_LEVEL=WARNING 2>&1 | tail -3
/tmp/appverify/bin/python -c "
import sqlite3; c=sqlite3.connect('/tmp/e2e_rents.db'); cur=c.cursor()
cur.execute('SELECT count(*) FROM listing_changes'); print('change rows after re-crawl (expect low/0 for unchanged listings):', cur.fetchone()[0])
"
```
Expected: a small number (ideally 0) — proves we don't log phantom changes for unchanged content.

- [ ] **Step 4: Record gate result (no commit)**

If all pass → proceed to Task 6 (deploy). If the crawler or a column fails, loop back to the relevant task.

---

## Task 6: Deploy to production and verify

**Files:** none modified.

**Interfaces:**
- Consumes: pushed `main` (Tasks 1-4) + passing gate (Task 5).
- Produces: running production container with the new schema, backfilled rooms, and history capture.

- [ ] **Step 1: Push main and wait for the image build**

```bash
git push origin main
sleep 8
RID=$(gh run list --repo porelli/craigscraper --workflow "Create and publish a Docker image" --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RID" --repo porelli/craigscraper --exit-status
gh run view "$RID" --repo porelli/craigscraper --json status,conclusion
```
Expected: `conclusion: success`.

- [ ] **Step 2: Back up the production DB**

```bash
ssh hpmini600g2 'sudo -n cp -v /datablind/containers-volumes/craigscraper/rents.db /datablind/containers-volumes/craigscraper/rents.db.bak-prehistory'
```
Expected: copy confirmation.

- [ ] **Step 3: Pull the new image, recreate the container**

```bash
ssh hpmini600g2 'docker pull ghcr.io/porelli/craigscraper:main | tail -1
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
Expected: container `Up`.

- [ ] **Step 4: Verify migration, backfill, crawl, and UI on the host**

```bash
ssh hpmini600g2 'sleep 50
echo "=== errors ==="; docker logs craigscraper 2>&1 | grep -icE "Traceback|ImportError|OperationalError"
echo "=== rooms backfill + schema ==="; docker exec craigscraper python3 -c "
import sqlite3; c=sqlite3.connect(\"/persist/rents.db\"); cur=c.cursor()
cur.execute(\"SELECT count(*), sum(bedrooms IS NULL) FROM listings\"); print(\"rows / null bedrooms:\", cur.fetchone())
cur.execute(\"SELECT name FROM sqlite_master WHERE type=\047table\047\"); print(\"tables:\", [r[0] for r in cur.fetchall()])
"
echo "=== UI ==="; curl -s -o /dev/null -w "UI HTTP %{http_code}\n" http://localhost:2352/'
```
Expected: error count 0; null bedrooms 0 (backfill ran on all 27,705); `listing_changes` in tables; UI HTTP 200.

- [ ] **Step 5: Rollback note (no action unless needed)**

If broken: recreate from the previous image and restore `rents.db` from `rents.db.bak-prehistory`.

---

## Self-Review Notes

- **Spec coverage:** rooms columns bedrooms/bathrooms/bathrooms_type + kept raw rooms (Task 2 Step 1) ✓; parse_rooms shared helper (Task 1) ✓; crawler populates fields (Task 3) ✓; rooms backfill of existing rows (Task 2 Step 3) ✓; listing_changes table + index (Task 2 Step 2) ✓; forward-only diffing on the 6 content fields, attributes as joined string, changed_at=last_updated (Task 2 Step 5) ✓; no history backfill (nothing seeds listing_changes) ✓; UI query-param anchor + @st.dialog (Task 4 Steps 3-4) ✓; unified price+field timeline with consecutive-price dedup, parameterized SQL (Task 4 Step 1) ✓; unit test as first test (Task 1) ✓; live + UI verification (Tasks 4-5) ✓; deploy via proven flow with DB backup (Task 6) ✓.
- **Placeholder scan:** none — every code/command step is concrete.
- **Type/name consistency:** `parse_rooms` returns dict with keys `bedrooms/bathrooms/bathrooms_type` used identically in Tasks 1/2/3; `get_listing_timeline`/`show_timeline_dialog` defined and called in Task 4; `?history=<id>` param written in create_price_trend and read in main().
- **Ordering note:** Task 2 uses `item.get('bedrooms')` so it tolerates Task 3 not yet done; but the plan orders Task 2 before Task 3, and both must ship together for the crawler to populate columns. Task 5 gate covers the combined result.

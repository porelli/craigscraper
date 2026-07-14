# Dependency Upgrade & Reproducible Locking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade all dependencies to latest (targeting Python 3.14) and lock the full transitive tree with hashes via pip-tools, so builds are reproducible and can never silently drift again.

**Architecture:** Split dependencies into a loose `requirements.in` (human-edited top-level list) and a generated, fully-pinned, hashed `requirements.txt` (the lockfile). The Dockerfile installs the lockfile with `--require-hashes` on `python:3.14`. Small UI code fixes remove APIs deleted in newer Streamlit. Everything is verified with a live crawl and a UI smoke test *before* the lock is generated and deployed.

**Tech Stack:** Python 3.14, Scrapy 2.17, Twisted (via Scrapy), pandas 3.0, Streamlit 1.59, plotly, pip-tools (build-time only), Docker, GitHub Actions, Watchtower.

## Global Constraints

- Target runtime: **Python 3.14** (`Dockerfile` base `python:3.14`). Fallback to `python:3.13` only if the UI cannot be made to work on pandas 3.0 / streamlit 1.59 quickly.
- `pip-tools` is **build-time only** — never added to `requirements.in`, never in the image.
- `requirements.txt` is a **generated lockfile** — never hand-edited after Task 3.
- Lockfile MUST be generated with `--generate-hashes`; Dockerfile MUST install with `--require-hashes`.
- The manual Twisted / pyOpenSSL / cryptography / service-identity pins added during the incident fix are **removed** from the top-level list (Scrapy 2.17 resolves compatible versions).
- Do NOT deploy until BOTH the crawler live-crawl and the UI 3-tab smoke test pass on Python 3.14.
- Before recreating the production container, back up `/datablind/containers-volumes/craigscraper/rents.db` on host `hpmini600g2` with `sudo cp`.
- Verify commands run in a Python 3.14 venv (`python3.14` is available locally).

---

## File Structure

- **`requirements.in`** — NEW. Loose top-level deps (the ~13 packages actually imported). Source of truth for *what* we depend on.
- **`requirements.txt`** — MODIFIED (becomes generated). Full transitive tree pinned with hashes. Produced by `pip-compile`.
- **`Dockerfile`** — MODIFIED. `FROM python:3.14`; install lockfile with `--require-hashes`.
- **`ui/ui.py`** — MODIFIED. Delete dead `handle_property_selection()`; replace `use_container_width=True` with `width="stretch"`.
- **`docs/superpowers/specs/2026-07-13-dependency-upgrade-and-lock-design.md`** — the approved spec (reference).

---

## Task 1: Create loose top-level `requirements.in`

**Files:**
- Create: `requirements.in`

**Interfaces:**
- Consumes: nothing.
- Produces: `requirements.in` — the input `pip-compile` reads in Task 3. Contains exactly the top-level packages the app imports, with no version pins (let the compiler resolve latest).

- [ ] **Step 1: Determine the true top-level deps**

The app imports these (from `craigscraper/`, `ui/ui.py`, `notifications.py`, `main.py`): scrapy, apprise, geopy, itemadapter, pandas, plotly, python-dotenv, regex_spm, schedule, streamlit, termcolor, scrapy-fake-useragent, setuptools. The incident-fix pins (Twisted, pyOpenSSL, cryptography, service-identity) are transitive → NOT listed here.

- [ ] **Step 2: Write `requirements.in`**

```
# Top-level dependencies only. This file is human-edited.
# The pinned, hashed lockfile is requirements.txt, generated with:
#   pip-compile --generate-hashes --upgrade requirements.in
# Do not hand-edit requirements.txt.

apprise
geopy
itemadapter
pandas
plotly
python-dotenv
regex_spm
schedule
Scrapy
setuptools
streamlit
termcolor
scrapy-fake-useragent
```

- [ ] **Step 3: Commit**

```bash
git add requirements.in
git commit -m "build(deps) add loose requirements.in as lock source of truth"
```

---

## Task 2: Fix UI code for newer Streamlit

**Files:**
- Modify: `ui/ui.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: a `ui/ui.py` that imports and runs under Streamlit 1.59 with no removed-API calls. Task 5 (UI smoke test) depends on this.

- [ ] **Step 1: Delete the dead `handle_property_selection()` function**

It uses the REMOVED `st.experimental_get_query_params()` / `st.experimental_set_query_params()` and is never called anywhere. Remove the entire function (the block starting `# Include this at the beginning...` through the end of `handle_property_selection`):

```python
# Include this at the beginning of your main function or at the top of the script
def handle_property_selection():
    """Process property selection via query parameters or session state"""
    # This helps with preserving the selected property across refreshes
    if 'selected_property_id' not in st.session_state:
        st.session_state.selected_property_id = None

    # Check for URL query parameters (for direct links)
    params = st.experimental_get_query_params()
    if 'property_id' in params:
        st.session_state.selected_property_id = params['property_id'][0]
        # Redirect to remove the query param to avoid issues with refreshes
        st.experimental_set_query_params()
```

Delete those lines entirely (leave the surrounding functions intact).

- [ ] **Step 2: Replace deprecated `use_container_width=True` with `width="stretch"`**

There are 6 call sites (1 `st.dataframe`, 5 `st.plotly_chart`). Replace each occurrence of `use_container_width=True` with `width="stretch"`. Use a find/replace across the file:

```bash
# from repo root
sed -i '' 's/use_container_width=True/width="stretch"/g' ui/ui.py
```

Then verify no occurrences remain:

```bash
grep -n "use_container_width" ui/ui.py
```
Expected: no output.

- [ ] **Step 3: Verify no removed APIs remain**

Run:
```bash
grep -n "experimental_get_query_params\|experimental_set_query_params\|handle_property_selection" ui/ui.py
```
Expected: no output.

- [ ] **Step 4: Byte-compile the file**

Run:
```bash
python3.14 -m py_compile ui/ui.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add ui/ui.py
git commit -m "fix(ui) remove dead experimental query-param code and deprecated use_container_width"
```

---

## Task 3: Generate the hashed lockfile on Python 3.14

**Files:**
- Modify: `requirements.txt` (replaced with generated content)

**Interfaces:**
- Consumes: `requirements.in` (Task 1).
- Produces: a fully-pinned, hashed `requirements.txt`. Task 4 (Dockerfile) and Tasks 4/5 (verification venv) install from it.

- [ ] **Step 1: Create a clean Python 3.14 tooling venv with pip-tools**

```bash
rm -rf /tmp/lockgen && python3.14 -m venv /tmp/lockgen
/tmp/lockgen/bin/pip install -q --upgrade pip pip-tools
/tmp/lockgen/bin/pip-compile --version
```
Expected: prints a `pip-compile, version ...` line.

- [ ] **Step 2: Compile the lockfile with hashes, upgrading to latest**

```bash
/tmp/lockgen/bin/pip-compile --generate-hashes --allow-unsafe --upgrade \
  --output-file requirements.txt requirements.in
```
Expected: writes `requirements.txt`; exit 0.

`--allow-unsafe` is REQUIRED: `setuptools` is a declared top-level dep (imported at runtime via `pkg_resources` in `notifications.py`). Without this flag, pip-compile leaves setuptools unpinned/commented, so the runtime would silently rely on whatever setuptools the base image ships — the exact drift this project eliminates. With the flag, setuptools is pinned and hashed like everything else.

- [ ] **Step 3: Sanity-check the generated lockfile**

```bash
grep -E "^scrapy==|^pandas==|^streamlit==|^twisted==" -i requirements.txt
grep -c "sha256:" requirements.txt
```
Expected: scrapy resolves to 2.17.x, pandas to 3.0.x, streamlit to 1.59.x, twisted present as a transitive pin; many `sha256:` hash lines.

- [ ] **Step 4: Verify the lock installs cleanly in a fresh 3.14 venv with --require-hashes**

```bash
rm -rf /tmp/lockverify && python3.14 -m venv /tmp/lockverify
/tmp/lockverify/bin/pip install -q --require-hashes -r requirements.txt
/tmp/lockverify/bin/python -c "import scrapy, pandas, streamlit, twisted, apprise, geopy, plotly, schedule, regex_spm, termcolor; print('all imports OK on', __import__('sys').version.split()[0])"
```
Expected: `all imports OK on 3.14.x`, no hash errors.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "build(deps) lock full dependency tree with hashes (pip-compile, py3.14)"
```

---

## Task 4: Update Dockerfile to Python 3.14 + hashed install

**Files:**
- Modify: `Dockerfile`

**Interfaces:**
- Consumes: `requirements.txt` (Task 3).
- Produces: an image definition that builds on 3.14 and fails loudly on any hash/lock mismatch.

- [ ] **Step 1: Rewrite the Dockerfile**

Current:
```dockerfile
FROM python:3.13

WORKDIR /app

COPY . .

RUN pip3 install -r requirements.txt

CMD ["bash", "run.sh"]
```

New (copy requirements first for layer caching; require hashes):
```dockerfile
FROM python:3.14

WORKDIR /app

COPY requirements.txt .

RUN pip3 install --require-hashes -r requirements.txt

COPY . .

CMD ["bash", "run.sh"]
```

- [ ] **Step 2: Build the image locally to confirm it succeeds**

```bash
docker build -t craigscraper:local-test .
```
Expected: build completes; the `pip3 install --require-hashes` step succeeds.

- [ ] **Step 3: Confirm Python version and imports inside the built image**

```bash
docker run --rm craigscraper:local-test python3 --version
docker run --rm craigscraper:local-test python3 -c "import scrapy, pandas, streamlit; print('img imports OK')"
```
Expected: `Python 3.14.x` and `img imports OK`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "build(docker) pin python 3.14 and install locked deps with --require-hashes"
```

---

## Task 5: Verify crawler + UI on the upgraded stack (GATE before deploy)

**Files:**
- None modified. This task is verification only. If it fails, fixes loop back to Task 2/3.

**Interfaces:**
- Consumes: `requirements.txt` (Task 3), `ui/ui.py` (Task 2).
- Produces: evidence (log output) that both the crawler and UI work on Python 3.14. This is the deploy gate.

- [ ] **Step 1: Build a verification venv from the lockfile**

```bash
rm -rf /tmp/appverify && python3.14 -m venv /tmp/appverify
/tmp/appverify/bin/pip install -q --require-hashes -r requirements.txt
```
Expected: exit 0.

- [ ] **Step 2: Run a bounded live crawl into a throwaway DB**

```bash
rm -f /tmp/upgrade_rents.db
env RENTS_DB=/tmp/upgrade_rents.db SUPPRESS_TEST_NOTIFICATION=True \
  /tmp/appverify/bin/scrapy crawl rent -s CLOSESPIDER_ITEMCOUNT=3 -s LOG_LEVEL=INFO 2>&1 \
  | grep -iE "item_scraped_count|finish_reason|log_count/ERROR|spider_exceptions|_setAcceptableProtocols|Traceback"
```
Expected: `item_scraped_count` > 0, `finish_reason: finished`, and NO `_setAcceptableProtocols` / `Traceback` / `spider_exceptions` lines.

- [ ] **Step 3: Confirm rows landed with all feature columns populated**

```bash
/tmp/appverify/bin/python -c "
import sqlite3; c=sqlite3.connect('/tmp/upgrade_rents.db'); cur=c.cursor()
cur.execute('SELECT count(*), sum(ev_charging is null), sum(gym is null), sum(parking is null), sum(pool is null) FROM listings')
print('rows / null(ev,gym,parking,pool):', cur.fetchone())
"
```
Expected: rows > 0, all null counts = 0.

- [ ] **Step 4: Smoke-test the UI headlessly against a real DB copy**

IMPORTANT: `ui/ui.py` resolves its DB as `/persist/rents.db` if that exists, else `./rents.db` in the cwd — it does NOT read the `RENTS_DB` env var. So place the DB at `./rents.db` for the smoke test. (Prefer a copy of the real prod DB if you can `scp` one; otherwise the crawl DB from Step 2 works.)

```bash
cd /Volumes/workspace/craigscraper
cp /tmp/upgrade_rents.db ./rents.db
/tmp/appverify/bin/streamlit run ui/ui.py --server.headless=true --server.port=8599 > /tmp/ui_smoke.log 2>&1 &
UI_PID=$!
sleep 12
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8599/
# surface only genuine errors (ignore the app's own benign "Error creating price trend" print)
grep -iE "Traceback|Exception|Error" /tmp/ui_smoke.log | grep -viE "error creating price trend" | head
kill $UI_PID 2>/dev/null || true
rm -f ./rents.db
```
Expected: `HTTP 200`, and no genuine `Traceback`/`Exception` lines in the log.

- [ ] **Step 5: Drive the 3 tabs (optional deeper check with Playwright, if available)**

If Playwright MCP is available, navigate to `http://localhost:8599/`, take a snapshot, click each of the three tabs ("Available Properties", "Price History", "Market Statistics"), and confirm each renders without an error banner. Otherwise, rely on Step 4's HTTP 200 + clean log as the smoke gate and note this in the deploy message.

- [ ] **Step 6: Record the verification result (no commit — this is a gate)**

If all pass → proceed to Task 6. If the UI fails on pandas 3.0 / streamlit 1.59 and the fix isn't quick, invoke the fallback: change `Dockerfile` to `FROM python:3.13`, re-run Task 3's `pip-compile` under `python3.13`, and re-verify. Document the fallback in the deploy commit message.

---

## Task 6: Deploy to production and verify on host

**Files:**
- None modified. Deploy of the merged `main` build.

**Interfaces:**
- Consumes: the pushed `main` branch (Tasks 1-4) and the passing gate (Task 5).
- Produces: a running production container on the upgraded stack.

- [ ] **Step 1: Push main and wait for the image build**

```bash
git push origin main
sleep 8
RID=$(gh run list --repo porelli/craigscraper --workflow "Create and publish a Docker image" --limit 1 --json databaseId --jq '.[0].databaseId')
echo "run: $RID"
gh run watch "$RID" --repo porelli/craigscraper --exit-status
gh run view "$RID" --repo porelli/craigscraper --json status,conclusion
```
Expected: `conclusion: success`.

- [ ] **Step 2: Back up the production DB on the host**

```bash
ssh hpmini600g2 'sudo -n cp -v /datablind/containers-volumes/craigscraper/rents.db /datablind/containers-volumes/craigscraper/rents.db.bak-preupgrade'
```
Expected: copy confirmation.

- [ ] **Step 3: Pull the new image and verify it's actually the 3.14 build**

```bash
ssh hpmini600g2 'docker pull ghcr.io/porelli/craigscraper:main | tail -2; docker run --rm ghcr.io/porelli/craigscraper:main python3 --version'
```
Expected: `Python 3.14.x`. If it still reports 3.13 (GHCR propagation lag), wait ~30s and re-pull until the digest/version updates.

- [ ] **Step 4: Recreate the container with the documented config**

```bash
ssh hpmini600g2 'docker stop craigscraper >/dev/null 2>&1 && docker rm craigscraper >/dev/null 2>&1
docker run -d --name craigscraper --restart unless-stopped -p 2352:8501 \
  -v /datablind/containers-volumes/craigscraper:/persist \
  -e MIN_PRICE=2000 -e MAX_PRICE=4000 -e LAT=49.2822 -e LON=-123.1284 \
  -e MIN_BEDROOMS=1 -e SEARCH_DISTANCE=1.41 \
  -e DISTANCE_FROM_LAT=49.2799016 -e DISTANCE_FROM_LON=-123.1167676 \
  -e RENTS_DB=/persist/rents.db -e SUPPRESS_TEST_NOTIFICATION=True \
  ghcr.io/porelli/craigscraper:main
sleep 3; docker ps --filter name=craigscraper --format "{{.Names}} {{.Status}} {{.Ports}}"'
```
Expected: container `Up`, port `2352->8501`.

- [ ] **Step 5: Verify a clean crawl in the running container**

```bash
ssh hpmini600g2 'sleep 45; echo "=== errors ==="; docker logs craigscraper 2>&1 | grep -icE "_setAcceptableProtocols|ImportError|Traceback"; echo "=== events ==="; docker logs craigscraper 2>&1 | grep -iE "finish_reason|item_scraped_count|is new|BREAKING" | head'
```
Expected: error count `0`; `finish_reason: finished` and `item_scraped_count` > 0.

- [ ] **Step 6: Verify the UI serves over HTTP**

```bash
ssh hpmini600g2 'curl -s -o /dev/null -w "UI HTTP %{http_code}\n" http://localhost:2352/'
```
Expected: `UI HTTP 200`.

- [ ] **Step 7: Note the rollback path in case of trouble**

If the container misbehaves, roll back by pulling the previous image digest (from `docker inspect` history or GHCR) and recreating, and restore the DB from `rents.db.bak-preupgrade`. No commit for this step.

---

## Task 7: Document the upgrade ritual

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a short documented procedure so future upgrades stay reproducible.

- [ ] **Step 1: Add a "Updating dependencies" subsection to README.md**

Under the existing "Local or dev" section, add:

```markdown
### Updating dependencies

Dependencies are locked in `requirements.txt` (generated, hashed) from `requirements.in`
(loose, human-edited). Never hand-edit `requirements.txt`. To upgrade:

1. install pip-tools in a throwaway env: `python3.14 -m venv /tmp/lock && /tmp/lock/bin/pip install pip-tools`
2. regenerate the lock: `/tmp/lock/bin/pip-compile --generate-hashes --allow-unsafe --upgrade requirements.in`
3. verify: build the image (`docker build .`) and run a bounded crawl + UI smoke test
4. commit `requirements.txt` and push (CI rebuilds the image)
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme) document the pip-tools dependency upgrade ritual"
```

---

## Self-Review Notes

- **Spec coverage:** requirements.in (Task 1) ✓; lockfile with hashes (Task 3) ✓; Dockerfile 3.14 + --require-hashes (Task 4) ✓; remove Twisted/OpenSSL manual pins (Task 1 omits them; Task 3 relocks) ✓; UI dead-code + use_container_width (Task 2) ✓; crawler+UI verification before deploy (Task 5 gate) ✓; deploy via proven path + DB backup (Task 6) ✓; fallback to 3.13 (Task 5 Step 6) ✓; upgrade ritual doc (Task 7) ✓; YAGNI items excluded ✓.
- **Placeholder scan:** no TBD/TODO; every code/command step is concrete.
- **Type/name consistency:** file paths and env values match the spec and the verified deploy config from the incident fix.
- **Known nuance captured:** `ui/ui.py` reads `/persist/rents.db` or `./rents.db` (not `RENTS_DB`) — Task 5 Step 4 accounts for this.

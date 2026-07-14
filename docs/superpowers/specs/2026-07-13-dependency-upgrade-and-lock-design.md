# Dependency Upgrade & Reproducible Locking — Design

**Date:** 2026-07-13
**Status:** Approved (pending spec review)

## Problem

The scraper silently died for ~3 weeks. The root cause of the *deploy* failure (distinct
from the Craigslist HTML change) was **floating dependencies**: `Dockerfile` used
`FROM python:3` (unpinned) and `requirements.txt` pinned only direct deps. A rebuild pulled
Python 3.14 + a newer Twisted that removed
`twisted.internet._sslverify._setAcceptableProtocols`, which Scrapy 2.12.0 imports — crashing
the crawler on startup. Nothing in the repo prevented this drift, and nothing detected it.

## Goal

Reproducibility-first. Two coupled changes:

1. **Upgrade all dependencies to latest** working versions, targeting **Python 3.14**.
2. **Introduce `pip-tools`** so the full transitive dependency tree is pinned with hashes.
   After this, dependency upgrades are always a deliberate, reviewed, committed action —
   never something a rebuild does silently.

The locking mechanism *is* the safety net. Being on latest is the immediate win; not drifting
again is the durable one.

## Approach: pip-tools with hashed lockfile

### File structure

- **`requirements.in`** (new, human-edited): the ~13 top-level packages actually used, with
  loose constraints only where deliberate. Source of truth for *what* we depend on.
- **`requirements.txt`** (now generated): full transitive tree pinned to exact versions with
  `--generate-hashes`. Produced by `pip-compile`. No longer hand-edited.
- **`Dockerfile`**: `FROM python:3.14`; install with `pip install --require-hashes -r requirements.txt`
  so a hash mismatch fails the build loudly instead of silently resolving something new.

### Upgrade command (documented for future use)

```
pip-compile --generate-hashes --upgrade requirements.in
```

Regenerates the lock; the diff is reviewed and committed. This is the entire "upgrade" ritual.

`pip-tools` is a **developer/build-time tool only** — it is NOT added to `requirements.in` and
does not ship in the image. It's installed ad hoc when regenerating the lock (`pipx install
pip-tools` or a throwaway venv). The runtime image only ever `pip install`s the hashed lockfile.

## Version bumps (verified: full stack installs & imports on Python 3.14)

| Package    | From    | To (latest at time of writing) | Notes |
|------------|---------|-------------------------------|-------|
| Python     | 3.13    | **3.14**                      | Dockerfile base image |
| Scrapy     | 2.12.0  | **2.17.0**                    | Removes the `_setAcceptableProtocols` break |
| Twisted    | 24.11.0 (manual pin) | latest via Scrapy | **Manual pin removed** — no longer needed |
| pyOpenSSL / cryptography / service-identity | manual pins | latest via Scrapy | **Manual pins removed** |
| pandas     | 2.2.3   | **3.0.x**                     | Major bump — UI is the risk area |
| streamlit  | 1.43.2  | **1.59.x**                    | Removed some `experimental_*` APIs |
| plotly     | 6.0.1   | latest                        | |
| apprise, geopy, itemadapter, python-dotenv, regex_spm, schedule, termcolor, scrapy-fake-useragent, setuptools | pinned | latest | |

The Twisted/pyOpenSSL/cryptography/service-identity manual pins added during the incident fix
are **removed** from the top-level list — Scrapy 2.17 pulls compatible versions itself, and
`pip-compile` locks whatever it resolves.

## UI code changes (required by the streamlit upgrade)

In `ui/ui.py`:

1. **Delete `handle_property_selection()`** (currently lines ~105-116). It calls the **removed**
   `st.experimental_get_query_params()` / `st.experimental_set_query_params()`. Verified it is
   **dead code** — defined but never called — so removal is zero-risk. (If query-param handling
   is ever wanted, the modern replacement is `st.query_params`.)
2. **Replace `use_container_width=True`** (6 call sites) with `width="stretch"` — the old form
   is deprecated and warns in current streamlit.

No other UI changes; all remaining imports and APIs verified working on the upgraded stack.

## Verification plan (must pass BEFORE locking & deploying)

1. **Crawler** — in a fresh Python 3.14 venv with the upgraded stack, run a bounded live crawl
   into a throwaway DB: `env RENTS_DB=/tmp/t.db SUPPRESS_TEST_NOTIFICATION=True scrapy crawl rent
   -s CLOSESPIDER_ITEMCOUNT=3`. Expect: items scraped, `finish_reason: finished`, no errors.
2. **UI** — `streamlit run ui/ui.py` against a copy of the real DB; load all three tabs
   (Available Properties, Price History, Market Statistics) and confirm no exceptions/tracebacks.
3. Only after both pass: `pip-compile` the lock, commit, rebuild image, deploy via the proven
   path (backup DB → push → build → pull-by-digest → recreate container → verify logs on host).

**Fallback:** if the UI breaks on pandas 3.0 / streamlit 1.59 in a way that isn't a quick fix,
fall back to latest-deps on Python 3.13 (already proven live) and defer 3.14. The lockfile work
is unaffected by this fallback.

## Deployment

Same host/flow validated during the incident fix (see memory: `deployment`):
- Host `hpmini600g2`, container `craigscraper`, image `ghcr.io/porelli/craigscraper:main`.
- Back up `/datablind/containers-volumes/craigscraper/rents.db` with `sudo cp` first.
- Push to `main` → GitHub Actions builds/publishes → `docker pull` (verify digest/python version,
  re-pull if GHCR propagation lag serves the old digest) → stop/rm/run with the documented env
  and volume → verify `docker logs` shows a clean crawl.

## Out of scope (YAGNI)

- Automated Dependabot/CI dependency upgrades.
- A test suite.
- Migrating to `uv` / `pyproject.toml`.

The lockfile plus a one-line documented upgrade command meets the reproducibility goal without
new machinery. These can be revisited later if desired.

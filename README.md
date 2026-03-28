# Granny

Granny is a Bay Area grocery-deals and shopping-planning project. This repository currently implements the deal ingestion pipeline, published-batch data model, local Flask app, admin operations dashboard, and local weekly scheduler.

## Scope Of This Repo

The broader product vision for Granny is larger than the code currently in this repository. The long-term vision includes route optimization, personalized shopping lists, and later pantry and meal-planning behavior.

Use this repository README as the source of truth for:
- what works in the current codebase
- how to run it locally
- how the pipeline and publish model work

Use [docs/prd-v0.md](docs/prd-v0.md) as the current milestone PRD for GitHub collaborators.
Use [CONTRIBUTING.md](CONTRIBUTING.md) for collaborator workflow expectations.

## What Works Today

- multi-store flyer ingestion for Safeway, H Mart, 99 Ranch, and Cardenas
- OCR/parsing and normalized deal import into SQLite
- published-batch model that keeps the last successful data live if a new run fails
- local Flask app with deal browser
- `/admin` operations dashboard
- chat-based shopping assistant prototype
- local weekly scheduling on macOS via `launchd`

## Not Yet Implemented

- route pre-computation and route optimization from the full PRD
- Google Shopping fallback for non-promoted items
- full conversation/batch-confirmation architecture
- production auth and hosted deployment
- pantry-aware or meal-planning features

## Repository Layout

- `app.py` — Flask app and API routes
- `database_utils.py` — batch schema, publish helpers, pipeline run tracking
- `run_weekly_pipeline.py` — top-level orchestrator
- `import_to_database.py` — deal import and auto-publish flow
- `download_*_flyer.py` — store-specific flyer acquisition
- `ocr_*_flyer.py` — OCR where applicable
- `parse_*_deals.py` — normalization and parsing logic
- `templates/` — server-rendered HTML
- `static/` — frontend assets
- `docs/` — project docs and current-scope PRD
- `scripts/` — helper scripts including scheduler install/uninstall
- `launchd/` — macOS scheduler template

## Setup

### Requirements

- Python 3.13 recommended
- macOS if you want to use the built-in `launchd` scheduler scripts

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment

Copy `.env.example` to `.env` and fill in the values you need.

Important variables:

- `OCR_PROVIDER`
- `VERTEX_SERVICE_ACCOUNT_FILE`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- provider API keys such as `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
- `FLIPP_ACCESS_TOKEN`
- `FLIPP_POSTAL_CODE`
- `CARDENAS_STORE_CODE`

Do not commit `.env`, service account JSON files, databases, logs, or generated flyer artifacts.

## Running The App

```bash
python3 app.py
```

Open:

- `http://localhost:8080/`
- `http://localhost:8080/admin`

## Running The Weekly Pipeline

Run the whole pipeline:

```bash
python3 run_weekly_pipeline.py
```

Run a single store import after parsing:

```bash
python3 import_to_database.py safeway
python3 import_to_database.py hmart
python3 import_to_database.py 99ranch
python3 import_to_database.py cardenas
```

## Publish Model

The app uses a batch-based publish model.

- each import run belongs to a batch
- only `published` batches are served by the app
- a successful run auto-publishes
- if a run fails, the previous published batch remains live
- pipeline step history is recorded in `pipeline_runs`

This is the main reliability mechanism for weekly automation.

## Scheduler

Local macOS scheduling files:

- `scripts/run_weekly_pipeline.sh`
- `scripts/install_launchd_job.sh`
- `scripts/uninstall_launchd_job.sh`
- `launchd/com.granny.weekly-pipeline.plist.template`

Install the default scheduler:

```bash
./scripts/install_launchd_job.sh
```

Default schedule is Wednesday at 6:00 AM local time.

Current behavior:

- one shared weekly scheduler runs the full pipeline for all supported stores
- it is not yet a true per-store cadence scheduler
- current automatic run time for Safeway, H Mart, 99 Ranch, and Cardenas is the same shared Wednesday 6:00 AM job

Logs:

```bash
logs/weekly_pipeline.out.log
logs/weekly_pipeline.err.log
```

## Collaboration Notes

- This repo is intended to be code-and-docs only.
- Generated flyer outputs, temp images, logs, and local databases should stay out of git.
- If README and older notes disagree, trust the current code and this README.
- The current milestone for collaborators is documented in [docs/prd-v0.md](docs/prd-v0.md).
- The ingestion automation design lives in [docs/design/weekly-ingestion-automation-design.md](docs/design/weekly-ingestion-automation-design.md).
- The early product pitch lives in [docs/vision/grocery-deal-proposal.md](docs/vision/grocery-deal-proposal.md).

## Current Priorities

- build the `item_price_history` database
- recompute historical price benchmarks on each pipeline run
- define observation thresholds and benchmark rules for price history
- improve automatic validation before publish
- make store refresh detection smarter
- build the actual optimization engine described in the broader product vision

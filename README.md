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

### Store-by-store status

| Store | Download method | Requires | OCR |
|---|---|---|---|
| H Mart | Playwright (browser automation) | — | Yes (image flyer) |
| 99 Ranch | Direct API (JSON) | — | No |
| Safeway | Flipp API | `FLIPP_ACCESS_TOKEN` | Yes (PDF flyer) |
| Cardenas | Flipp API | `FLIPP_ACCESS_TOKEN` | No |

H Mart and 99 Ranch work out of the box. Safeway and Cardenas require a Flipp API token (not publicly available) and will fail at the download step without it — this does not affect the other two stores.

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
python -m playwright install chromium
```

> `python -m playwright install chromium` is required for store flyer downloads that use browser automation (H Mart and 99 Ranch). Run it once after setting up the virtual environment. Using `python -m playwright` (instead of the bare `playwright` command) ensures the correct browser build for the active virtual environment is installed.

### Environment

Copy `.env.example` to `.env` and fill in the values you need.

#### OCR provider (required — pick one)

`OCR_PROVIDER` selects which vision model is used to read flyer images. Set exactly one of the following:

| `OCR_PROVIDER` value | Key required | Notes |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` | Easiest Google option, no service account needed |
| `openai` | `OPENAI_API_KEY` | GPT-4o |
| `anthropic` | `ANTHROPIC_API_KEY` | Claude |
| `openrouter` | `OPENROUTER_API_KEY` | Proxy — uses `google/gemini-2.0-flash-001` by default |
| `deepseek` | `DEEPSEEK_API_KEY` | DeepSeek VL2 |
| `novita` | `NOVITA_API_KEY` | DeepSeek OCR-2 via Novita |
| `vertex` | `VERTEX_SERVICE_ACCOUNT_FILE` + `GOOGLE_CLOUD_PROJECT` | Default in `.env.example`; requires a GCP service account JSON file |

> Only stores that download image flyers (H Mart, Safeway) consume OCR credits. 99 Ranch uses a direct JSON API and does not call the OCR provider.

#### Other variables

- `FLIPP_POSTAL_CODE` — zip code for Flipp flyer lookup (default: `94086`)
- `FLIPP_ACCESS_TOKEN` — required only for Safeway and Cardenas (not publicly available)
- `CARDENAS_STORE_CODE` — Cardenas store number (default: `215`)

Do not commit `.env`, service account JSON files, databases, logs, or generated flyer artifacts.

## First-Time Usage

**Always run the pipeline before starting the app.** The app reads from the database; if you start it before any data has been imported, every page will error with `no such table: deals` or return empty results.

```bash
# Step 1 — ingest data for the stores you have access to
python3 run_weekly_pipeline.py

# Step 2 — start the web app
python3 app.py
```

Open:

- `http://localhost:8080/` — deal browser
- `http://localhost:8080/admin` — operations dashboard

### Running a single store

```bash
python3 run_weekly_pipeline.py hmart
python3 run_weekly_pipeline.py 99ranch
```

### Re-importing after a parse fix (skip download and OCR)

```bash
python3 import_to_database.py hmart
python3 import_to_database.py 99ranch
```

### Pipeline stages per store

Each store runs through up to four stages in order. A failure at any stage stops that store and moves on to the next.

| Stage | H Mart | 99 Ranch | Safeway | Cardenas |
|---|---|---|---|---|
| Download | Playwright scrape | Direct API | Flipp API ⚠️ | Flipp API ⚠️ |
| OCR | Yes (image) | — (skipped) | Yes (PDF) | — |
| Parse | Yes | Yes | Yes | Yes |
| Import | Yes | Yes | Yes | Yes |

⚠️ Requires `FLIPP_ACCESS_TOKEN`. The store is skipped if the token is missing; other stores continue normally.

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

# Contributing

## First Read

Start here before making changes:

1. Read [README.md](/Users/luna/Desktop/Granny/Granny%20code/README.md)
2. Read [docs/prd-v0.md](/Users/luna/Desktop/Granny/Granny%20code/docs/prd-v0.md)
3. If you need the broader vision, read the full Granny PRD outside this repo

## What This Repo Is

This repository is the current implementation and collaboration hub for:

- deal ingestion
- parsing and normalization
- published-batch database behavior
- local Flask app and admin dashboard
- local weekly scheduling

It is not yet the full optimization product described in the broader PRD.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your local credentials and config.

## Working Rules

- Do not commit `.env`, credentials, databases, logs, or generated flyer artifacts.
- Treat the repo as code-and-docs only.
- Keep top-level entrypoints stable unless there is a strong reason to change them.
- Prefer small, reviewable changes.
- Update docs when behavior changes.

## Important Commands

Run the app:

```bash
python3 app.py
```

Run the pipeline:

```bash
python3 run_weekly_pipeline.py
```

Install the local scheduler:

```bash
./scripts/install_launchd_job.sh
```

## Current Priorities

- build `item_price_history`
- improve publish validation
- improve store refresh detection
- continue toward the optimization engine

## Pull Requests / Commits

- Use clear commit messages
- Mention user-visible or operational impact
- If changing data flow or scheduler behavior, update README

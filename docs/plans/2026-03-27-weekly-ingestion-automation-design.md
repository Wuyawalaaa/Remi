# Weekly Ingestion Automation Design

## Goal

Automate the weekly deal ingestion pipeline so new flyer data is fetched and published without manual intervention, while keeping the site stable when a run fails. Admin should provide visibility into pipeline health and published data, but should not block publishing.

## Current Problem

The project currently relies on manual reruns of store-specific download, OCR, parse, and import scripts. When nobody reruns the pipeline after a weekly flyer refresh, the database can contain no currently active deals even though the app itself still works.

## Desired Behavior

- A scheduled job runs weekly.
- New data is published automatically when a store run succeeds.
- If a store run fails, the previous successful published data for that store remains live.
- Admin shows operational status, recent runs, and batch quality signals.
- The design should work locally first and migrate cleanly to a hosted scheduler later.

## Recommended Architecture

Keep the existing store-specific scripts and add a lightweight orchestration layer above them.

### Existing scripts kept in place

- `download_*_flyer.py`
- `ocr_*_flyer.py`
- `parse_*_deals.py`
- `import_to_database.py`

### New orchestrator

Add a top-level runner, for example `run_weekly_pipeline.py`, that:

1. Iterates through supported stores.
2. Detects whether a new flyer is available.
3. Creates a new batch record for the run.
4. Executes the existing store pipeline steps in order.
5. Imports all resulting deals with a `batch_id`.
6. Runs automatic sanity checks on the imported data.
7. Publishes the batch immediately if checks pass.
8. Marks the batch failed and keeps the old published data if checks fail.

This keeps the current implementation reusable while introducing a reliable publish model.

## Data Model Changes

### `batches`

Tracks one import attempt for a store and flyer window.

Suggested columns:

- `id`
- `store_name`
- `valid_from`
- `valid_to`
- `status`
- `created_at`
- `published_at`
- `row_count`
- `warning_count`
- `error_count`
- `source_flyer_path`

Suggested statuses:

- `running`
- `published`
- `failed`
- `superseded`

### `pipeline_runs`

Tracks step-level execution status for visibility and debugging.

Suggested columns:

- `id`
- `batch_id`
- `store_name`
- `step_name`
- `status`
- `started_at`
- `finished_at`
- `message`

### `deals.batch_id`

Add `batch_id` to `deals` so every imported row is tied to one specific run. `valid_to` describes business validity, but `batch_id` identifies the operational import snapshot that created the row.

## Publish Model

The app should query only the latest `published` batch per store.

Per store:

1. Insert a `batches` row with `status = running`.
2. Run download, OCR, parse, and import.
3. Import rows into `deals` tagged with the batch's `id`.
4. Run validation checks.
5. If checks pass:
   - mark the previously published batch for that store as `superseded`
   - mark the new batch as `published`
   - record `published_at`
6. If checks fail:
   - mark the batch as `failed`
   - leave the previous published batch unchanged

This gives automatic publishing without risking a blank or half-broken site.

## Validation Rules

The first version should use simple operational checks rather than manual approval.

Recommended checks:

- row count is above a minimum threshold per store
- `valid_from` and `valid_to` are present
- row count is not drastically below the prior published batch
- missing normalized price rate is below a warning threshold
- parse/import did not raise hard errors

Checks should support warnings and failures:

- warnings surface in admin but still allow publish
- failures prevent publish for that store

## Admin Dashboard Role

Admin is an operations dashboard, not a publish gate.

It should show:

- current published batch per store
- last run time and status per store
- last successful publish time
- row counts and deltas versus previous published batch
- warning counts and failure messages
- links or references to raw flyer, parsed JSON, and logs when available

Optional later additions:

- rerun failed store
- rollback to previous published batch
- force publish

These are useful but not required for the first automation milestone.

## Scheduler Strategy

### Phase 1: local

Run the orchestrator from a weekly `launchd` job on the Mac.

### Phase 2: hosted

Run the exact same orchestrator command in a hosted cron or worker environment.

Design requirement:

- scheduling only triggers the orchestrator
- publish rules remain in application/database logic
- the same code path should work locally and in hosted infrastructure

This avoids a migration rewrite later.

## App Query Changes

Update app data reads so user-facing endpoints only return deals from the latest published batch for each store. This applies to:

- `/`
- `/api/deals`
- `/api/chat`
- `/admin` summary widgets that describe live data

Admin can additionally inspect failed or superseded batches separately.

## Failure Behavior

- If one store succeeds and another fails, successful stores can publish independently.
- A failed store continues serving its last published batch.
- If no previous published batch exists for a store, that store simply has no live data until its first successful run.
- Batch records and step records should preserve enough detail to diagnose failures without reading code.

## Implementation Sequence

1. Add schema changes for `batches`, `pipeline_runs`, and `deals.batch_id`.
2. Update import flow to attach rows to a batch.
3. Update read queries to select only published batches.
4. Build `run_weekly_pipeline.py`.
5. Add automatic validation and publish/supersede logic.
6. Extend `/admin` to show operational status.
7. Add local weekly scheduling.
8. Reuse the same runner in a hosted environment later.

## Non-Goals For V1

- manual review as a publish requirement
- global all-stores atomic publishing
- distributed queue infrastructure
- object storage migration
- per-row history beyond batch attribution

## Success Criteria

- Weekly ingestion happens automatically.
- Successful runs publish fresh data without human action.
- Failed runs do not remove existing live deals.
- Admin gives enough insight to spot stale stores, bad runs, and data-quality regressions.
- The same orchestration path can later move from local scheduling to hosted scheduling.

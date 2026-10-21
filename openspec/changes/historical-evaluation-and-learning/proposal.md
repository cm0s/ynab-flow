# Proposal

## Why

Phase 1 made imports safe but never measured whether payee and category suggestions are right: confidence scores are explicitly not accuracy. The household now has a full YNAB history export (about 18,100 register lines, 2017–2026, 12 accounts) that can answer that question, and the classifier needs a way to improve from user corrections without learning from its own unreviewed guesses or breaking when categories are renamed.

## What Changes

- Add a local, reproducible evaluation command that replays history through the production classification pipeline using a chronological split. The command reads the ignored YNAB export or the synced local history, never writes to YNAB, and never commits private data or reports.
- Guarantee isolation: evaluation rows cannot reach the exact-history index, the fuzzy index, ML training or derived rules used for that run; user-authored rules are evaluated separately and labelled as written with hindsight.
- Classify every excluded or special row (transfers, split lines, uncategorized, hidden or deleted categories, blank descriptions, duplicates, refunds, starting balances) with a reason and a count instead of silently dropping it.
- Report payee and category accuracy separately, broken down per account, per prediction source and per confidence band. Also report calibration, auto-accept precision, review workload and the worst failures. State that labels reflect present-day assignments.
- Identify categories internally by stable YNAB category ID for training, history matching and write-back, while showing current names; renames no longer fragment training data or break resolution.
- Record the provenance of each synced transaction. Transactions created by this app and still unapproved in YNAB are machine guesses and are excluded from training. Approved ones count as user-confirmed, and a later change in YNAB versus what was sent counts as a correction.
- Replace overwrite-on-train with a candidate/promotion policy: train on trusted examples only, compare the candidate against the active model on a recent held-out slice, promote only when it is not worse, keep the previous model for rollback and record what each model was trained on.
- Record whether a synced transaction is a split parent, and keep split parents out of training until split support exists.

## Capabilities

### New Capabilities
- `classification-evaluation`: offline, reproducible measurement of payee/category prediction quality on historical data, with leakage isolation, explicit case handling and disclosed limitations.
- `model-training-lifecycle`: which examples are trusted for learning, how corrections and machine guesses are distinguished, and how trained models are promoted, versioned and rolled back.

### Modified Capabilities
- `transaction-classification`: categories are identified by stable category ID across training, matching, suggestion and write-back; display names are resolved at the edge.

## Impact

- Backend: new evaluation module and CLI entry point; `ml_classifier.py` (ID labels, injectable model directory, candidate/active models); `historical_matcher.py` (ID labels); `orchestrator.py` and import rows (category ID alongside name); `write_back_service.py` (send the stored ID instead of resolving by name); `sync_service.py` (provenance and split flag); `main.py` (`/train` becomes candidate training plus promotion; model status endpoint).
- Database: additive Alembic migration for transaction provenance, split flag, row category ID and a model registry table; backfill of category IDs on existing import rows where a unique name resolves.
- Files: model artifacts move to versioned files under `backend/models/` with the active version recorded; evaluation reports are written to an ignored directory.
- Frontend: the review table's category picker sends the chosen category's ID with its name; a small model status panel in Settings shows the active version and the last training comparison, with a restore action.
- No change to the write path's safety guarantees or to thresholds.
- Out of scope: Proton receipts and cloud AI (Phase 3), split write support, automatic threshold changes, and scheduled background retraining.

# Tasks

## 1. Evaluation harness

- [x] 1.1 Add a `model_dir` parameter to `MLClassifier` (default unchanged) and verify with a test that training into a temporary directory leaves `backend/models/` untouched
- [x] 1.2 Implement the canonical evaluation row and the `export` adapter (zip or extracted `Register.csv`, DD.MM.YYYY dates, outflow/inflow to milliunits) and verify with a synthetic export fixture test, including a missing/unrecognised file error
- [x] 1.3 Implement the `synced` adapter reading local transactions read-only and verify with a test that it produces the same canonical rows for equivalent data
- [x] 1.4 Implement the treatment taxonomy (D3) with transfers as payee-only, the refund and hidden-category tags and the duplicate count, and verify with a test that every fixture row gets exactly one treatment and the counts sum to the input
- [x] 1.5 Implement the chronological split (80% default, `--cutoff` override) and verify with a test that all training dates precede all evaluation dates and the ranges are reported
- [x] 1.6 Build the disposable training database and temporary model directory (D1), run `Orchestrator.predict` per evaluation row, and verify with a leakage test that a description present only in the evaluation period never gets its own label back from history or ML
- [x] 1.7 Add the `--with-rules` variant copying current rules into the temporary database and verify with a test that its results are reported separately and labelled as hindsight
- [x] 1.8 Compute the metrics (D5): per-target accuracy, per-account, per-source and per-band breakdowns, coverage, auto-accept precision, corrections required, top confident failures and frequent confusions. Verify with a test on a hand-computed fixture
- [x] 1.9 Write JSON and Markdown reports with input SHA-256, cut-off, options, git commit and the label-limitation statement to `backend/reports/evaluation/`, add `backend/reports/` to `.gitignore`, and verify with a test that two runs give identical metrics
- [x] 1.10 Add the CLI entry point (`python -m src.evaluation ...`, with `--max-rows` and progress output), document it in `docs/classification-evaluation.md`, and verify the documented command runs on the synthetic fixture

## 2. Baseline measurement

- [x] 2.1 Run the evaluation on the private export with the current (name-based) classifier, learned-only and with rules. Keep the reports in the ignored directory and record the headline numbers (no merchant data) in the change's notes for later comparison

## 3. Stable category IDs

- [x] 3.1 Add an additive migration for `import_rows.category_id`/`edited_category_id`, `transactions.provenance`/`is_split` and the `model_versions` table, with a category-ID backfill for unambiguous names. Verify the upgrade/downgrade tests on SQLite and a check on a disposable PostgreSQL
- [x] 3.2 Add the `CategoryDirectory` and switch the history matcher and ML labels to local category IDs, and verify with a test that renaming a category keeps its history and predictions (rename scenario)
- [x] 3.3 Carry `category_id` through `PredictionResult`, upload, reclassify and the batch payload, keeping `category` as the display name, and verify with the existing import API tests plus an ID assertion
- [x] 3.4 Accept `edited_category_id` in `PATCH /import-rows` and use stored IDs in write-back with the legacy name fallback, blocking deleted categories. Verify with tests for the "write-back uses the stored ID" and "category no longer exists" scenarios
- [x] 3.5 Update the review table's category picker to send the category ID, and verify `pnpm run build` and the flow tests pass
- [x] 3.6 Update the evaluation to compare category labels by ID and verify its tests still pass

## 4. Provenance and trusted training

- [x] 4.1 Store `is_split` from `subtransactions` and derive `provenance` during sync (D7), and verify with sync tests for the unapproved, approved, corrected, external and split cases
- [x] 4.2 Restrict ML and history training inputs to trusted examples, report the exclusion counts in the training statistics, and verify with a test that an unapproved app transaction and a split parent are not learned

## 5. Model promotion and rollback

- [x] 5.1 Implement the version registry and versioned model directories, register legacy model files as version 1 on startup, and verify with a test that an existing install keeps predicting without retraining
- [x] 5.2 Implement candidate training with the recent 10% holdout, the comparison, promotion or rejection, retrain-on-all after promotion and file retention. Verify with tests for the worse-candidate, first-model and concurrent-training scenarios
- [x] 5.3 Add `GET /models` and `POST /models/{id}/activate`, plus startup recovery of a missing active model, and verify with tests for rollback and missing-file recovery
- [x] 5.4 Add the Settings model status panel (active version, last comparison, restore previous), and verify `pnpm run build`
- [x] 5.5 Document training, promotion and rollback in `docs/classification-evaluation.md`, and verify the documented endpoints match the API

## 6. Integration

- [x] 6.1 Run the full backend suite (`pytest tests/ -v`), `pnpm run build` and the flow tests, and verify all pass
- [x] 6.2 Re-run the private evaluation after the changes, compare it with the 2.1 baseline, and report the differences to the user without committing private reports

## Workflow follow-up

- Propose a commit and wait for the user's approval (AGENTS.md).
- Archive the change after review.

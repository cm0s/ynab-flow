# Design

## Context

- Classification runs rules → `HistoricalMatcher` → `MLClassifier` in `Orchestrator`. Both learned stages read the `transactions` table directly: the matcher builds its exact and fuzzy indexes in its constructor, and `MLClassifier.train` queries the table and overwrites `backend/models/{payee,category}_model.joblib`. A missing model triggers training at startup.
- Labels are names. The matcher and ML return category names, import rows store `category`/`edited_category` as names, the review picker edits by name, and write-back resolves a name to an ID with `.first()` among same-named categories. Today no category name appears in two groups, but renames split history.
- The YNAB plan export's `Register.csv` has about 18,100 lines dated 2017–2026 across 12 accounts. It has no IDs: it contains account, payee, "Group: Category", memo, outflow/inflow (plain decimals), date (DD.MM.YYYY) and cleared status. Splits are flattened into lines whose memo starts with `Split (n/m)`, and transfers have a `Transfer : <account>` payee. There are about 1,530 split lines, 502 transfers, about 2,150 blank memos and 720 lines in Hidden Categories.
- Sync stores `approved` and `import_id` but ignores subtransactions. Since Phase 1, app-created transactions carry `YF1:` import IDs and are sent unapproved, and `import_rows.sent_payee`/`sent_category` record what was sent.
- The hosted backend runs on Render, which has an ephemeral disk. Model files there are rebuilt at startup.

## Goals / Non-Goals

**Goals:** evaluation runs the same production code path that serves real predictions, so measured numbers describe the real classifier. Category identity is moved to IDs without breaking stored batches. Model promotion is safe on both SQLite and PostgreSQL.

**Non-Goals:**
- Tuning thresholds or model hyperparameters.
- Payee IDs: payees are often created from imported names and have no stable identity before the push.
- Evaluating the user's rules for correctness.
- Persisting model binaries in the database.

## Decisions

### D1. Evaluate through the real pipeline on a disposable database
The evaluation loads the training slice into a temporary SQLite database with the production schema. It creates one synthetic plan and an account, payee and category per distinct export value, using deterministic synthetic IDs derived from the label text. It then trains an `MLClassifier` into a temporary model directory, and calls `Orchestrator.predict` for each evaluation row with its account and currency amount.
- *Why:* the matcher, normalizer, ML pipeline and thresholds are exercised unchanged, and isolation is structural, because evaluation rows never enter that database.
- *Alternative:* re-implement scoring outside the app. Rejected, because it would measure a copy rather than the app.
- To support this, `MLClassifier` takes a `model_dir` (default unchanged), and nothing in the evaluation imports the production `SessionLocal`.

### D2. Two input adapters with one canonical row
- `export` adapter: reads `Register.csv` from the zip, or an extracted CSV.
- `synced` adapter: reads the local database's transactions, read-only.

Both yield the same canonical row: date, account, payee, category label, memo, milliunits, flags. The export is the default because it is the complete, user-provided history. The synced adapter enables evaluation with real category IDs and provenance.

### D3. The split and the exclusion taxonomy are explicit data
- **Cut-off:** defaults to the date at which 80% of eligible rows fall before. It can be overridden with `--cutoff YYYY-MM-DD`.
- **Treatment:** each row gets exactly one treatment from a fixed enum.
  - Evaluated: `evaluated`, `evaluated_payee_only` (transfers).
  - Excluded: `excluded_split_line`, `excluded_uncategorized`, `excluded_starting_balance`, `excluded_blank_description`. Usable rows dated before the cut-off train and the rest are evaluated; the same treatments apply to both periods.
  - Subsets: `refund` (an inflow with a non-income category) and `hidden_category` are tags on evaluated rows, not exclusions.
- **Duplicates:** identical rows (same date, account, amount and memo) are kept and evaluated individually. Their count is reported, because removing real repeat purchases would bias results.

### D4. Rules are an opt-in variant
The default run uses no rules, which gives learned-only results. `--with-rules` copies the user's current rules from the local database into the temporary one and reports a second result set labelled "rules written with hindsight". Phase 1 has no rule derivation, so there is nothing else to isolate.

### D5. Report format
- **JSON:** `reports/evaluation/<timestamp>-<shortfingerprint>.json` holds the metrics, breakdowns, failures, configuration, the input SHA-256 and the git commit.
- **Markdown:** a summary alongside it.
- **Location:** `backend/reports/` is added to `.gitignore`.
- **Metrics:** accuracy is exact-match on the label (case-sensitive ID for categories, normalized name for payees). Calibration buckets use the existing confidence bands. "Corrections required" counts rows where any required target is wrong or missing.
- **Determinism:** the ML pipeline already uses a fixed `random_state`. Rows are processed in a stable order (date, then input line number).

### D6. Category IDs everywhere, names at the edge
- The matcher index and ML labels use the local `Category.id`. When a name is needed, it is mapped through a `CategoryDirectory` that is loaded once per request.
- `PredictionResult` gains `category_id` and keeps `category` as the display name.
- `import_rows` gains `category_id` and `edited_category_id`.
- `PATCH /import-rows` accepts `edited_category_id`. The review picker already has the category list with IDs and sends both.
- Write-back uses `edited_category_id` or `category_id` → `ynab_category_id`. If neither is set, it falls back to the old name lookup for legacy rows. A deleted category blocks the row with a review error.
- A migration backfills `category_id` and `edited_category_id` where the stored name resolves to exactly one non-deleted category in the batch's plan, and leaves them null otherwise.
- *Alternative:* store the YNAB category ID directly. Rejected for internal joins, because the local ID is what history references. `ynab_category_id` is used only at the API boundary.

### D7. Provenance is derived at sync time
- A `transactions.provenance` column takes the values `app_unreviewed`, `app_confirmed`, `app_corrected`, `external` and `split_parent`.
- **Rule:** if a transaction has subtransactions, it is `split_parent`. Otherwise, if its import ID starts with `YF1:`: unapproved gives `app_unreviewed`; approved with a category or payee differing from the linked row's `sent_*` gives `app_corrected`; approved otherwise gives `app_confirmed`. Anything else is `external`.
- Sync stores `is_split` from the API's `subtransactions` field.
- **Trusted for training:** `external`, `app_confirmed` and `app_corrected`. App corrections carry the corrected label, so they are learned.

### D8. Candidate, promotion and registry
- **Registry:** a `model_versions` table records id, created_at, status (`candidate`, `active`, `retired`, `rejected`), training counts, exclusion counts, held-out metrics and the comparison note.
- **Files:** binaries live at `backend/models/<version>/{payee,category}.joblib`, and the active version is the registry row with status `active`.
- **Training flow:** `POST /train` holds out the most recent 10% of trusted examples by date, trains the candidate on the rest, and scores both the candidate and the active model on the holdout. It promotes if the candidate is not worse on every trained target; ties promote, so new data is adopted. It then retrains the promoted model on all trusted examples, so the holdout is not wasted. The result is returned.
- **Rollback:** `POST /models/{id}/activate` restores a retired version whose files still exist. `GET /models` lists versions.
- **Retention:** the active version and the last two retired versions are kept on disk.
- **Hosted:** on startup, if the active version's files are missing (Render's ephemeral disk), the active version is retrained from trusted examples and its registry entry is updated. It is not silently replaced by an unrecorded model.
- *Alternative:* compare against a fixed benchmark set. Rejected, because a recent holdout tracks the household's current behaviour.

## Risks / Trade-offs

- [Export labels are present-day assignments] → stated in every report (spec). The synced adapter offers ID-based labels as a cross-check.
- [Memo in the export may contain user notes rather than bank text] → reported as a limitation; blank memos are excluded and counted.
- [The holdout comparison is noisy for rare categories] → overall accuracy per target is compared, not per class; the note records the sample size and warns when the holdout has fewer than 50 examples.
- [Backfill cannot resolve renamed or ambiguous names on old rows] → those rows keep the name-based fallback at write-back, and unresolved counts are logged by the migration.
- [Retrain-on-all after promotion means the deployed model differs slightly from the evaluated one] → recorded in the version entry; the evaluation command remains the authoritative measurement.
- [Evaluation on 18k rows may be slow, because the fuzzy matcher is O(n) per row] → acceptable for an offline command. Progress is shown and the run can be limited with `--max-rows` for quick checks.

## Migration Plan

1. One additive Alembic revision adds the following, then backfills `provenance` from existing `import_id`/`approved` values and category IDs on import rows:
   - `transactions.provenance` and `transactions.is_split`
   - `import_rows.category_id` and `import_rows.edited_category_id`
   - the `model_versions` table
2. On first startup after deploy, existing `models/*.joblib` files are registered as version 1, `active`, with unknown metrics, so nothing retrains unexpectedly.
3. Rollback: downgrade drops the new columns and table; legacy model files are left in place.

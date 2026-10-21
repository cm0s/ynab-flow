# Classification evaluation

Measures how well payee and category suggestions match your real history. It runs locally, never contacts YNAB, and never changes the app database or its active models.

## Run it

From `backend/` with the virtualenv active:

```bash
# A YNAB plan export (the .zip from "Export plan"), or its extracted "... - Register.csv"
python -m src.evaluation --export ../ynab-automation/data/ynab-history-export.zip

# Also score your current rules (reported separately; written with hindsight)
python -m src.evaluation --export ../ynab-automation/data/ynab-history-export.zip --with-rules

# Synced local history of a plan instead of an export (uses real category IDs)
python -m src.evaluation --synced <plan id>
```

Options:

| Option | Meaning |
|---|---|
| `--cutoff YYYY-MM-DD` | First evaluation date. The default is the date before which 80% of usable rows fall. |
| `--with-rules` | Adds a `with_rules` variant using the rules in the local database. |
| `--max-rows N` | Scores only the first N evaluation rows, for a quick check. |
| `--out-dir PATH` | Report directory. The default is `backend/reports/evaluation/`, which is git-ignored. |
| `--quiet` | No progress output. |

Each run writes `<timestamp>-<input hash>.json` (machine-readable, comparable between runs) and a `.md` summary. Reports contain descriptions from your history, so they stay in the ignored directory.

## How it works

1. **Read:** every register line becomes a canonical row with date, account, payee, category, memo and amount.
2. **Treat:** each row gets exactly one treatment, and the counts are reported.
   - **Evaluated:** ordinary categorized rows.
   - **Payee only:** transfers (`Transfer : …` payees) carry no ordinary category, so only their payee is scored.
   - **Excluded:**
     - starting balances
     - split lines (`Split (n/m)` memos), because their bank description belongs to the whole transaction
     - blank descriptions
     - uncategorized rows
   - **Subsets reported separately:** refunds (inflows outside income categories) and Hidden Categories.
   - **Duplicates:** identical rows are kept, because real repeat purchases exist, and their number is reported.
3. **Split:** usable rows dated before the cut-off are training rows. The rest are evaluated in date order.
4. **Isolate:** only training rows are loaded into a temporary database and a temporary model directory. The real pipeline (normalizer, rules, exact and fuzzy history, ML, thresholds) then predicts each evaluation row, so evaluated rows can never be their own evidence.
5. **Measure:** payee and category are scored separately.
   - **Accuracy:** a missing suggestion counts as wrong.
   - **Coverage**
   - **Breakdowns:** per account, per prediction source and per confidence band (`none`, `low` < 0.75, `medium` < 0.95, `high`).
   - **Auto-accept precision:** rows the app would accept without review.
   - **Corrections required**
   - **Mistakes:** the most confident mistakes and the most frequent confusions.

## Limitations

- Labels are the present-day assignments in the export, not necessarily what was chosen at the time. Renamed categories appear under their current names.
- Accuracy is measured on this history and is not a promise for future imports.
- Export descriptions are YNAB memos, which can contain notes instead of bank text.
- Identical input, cut-off, options and code give identical metrics. The report records the input SHA-256 and the git commit.

## Calibration (October 2026)

The first evaluation showed that rows the app auto-accepted (both targets ≥ 0.95) were right only 88.9% of the time on the export (76.5% on the synced history). Two sources of over-confidence were measured:

- **Fuzzy history** could reach 0.95. Those rows were wrong about 22% of the time.
- **Exact history backed by only two agreeing examples** scored 0.95.

Fuzzy confidence is now capped at 0.94, and exact history stays at 0.94 or below until at least three examples agree. Both still suggest a payee and category; they just need review. The rule was chosen on the 2024-12-19 → 2026-10 evaluation period and checked on a separate period (training before 2023-01-01, first 2,000 rows after):

| Period | Before: auto-accepted, precision | After: auto-accepted, precision |
|---|---|---|
| 2023 validation | 256 rows, 85.9% | 132 rows, 100% |
| 2024-12 → 2026-10 | 727 rows, 88.9% | 510 rows, 92.3% |

Accuracy itself is unchanged, because predictions are the same and only review eligibility changed. Most remaining auto-accept mistakes are payees or categories renamed after the matching history, which no confidence rule can predict.

# Training, promotion and rollback

## What the classifier learns from

Sync records a provenance for every transaction:

| Provenance | Meaning | Learned? |
|---|---|---|
| `external` | Not created by this app (entered in YNAB, file imports, bank links) | yes |
| `app_confirmed` | Created by this app and approved in YNAB unchanged | yes |
| `app_corrected` | Created by this app, approved, and its payee or category was changed in YNAB (compared with what was sent) | yes, with the corrected label |
| `app_unreviewed` | Created by this app and still unapproved in YNAB: a machine guess nobody has checked | no |
| `split_parent` | Has subtransactions; its single category is not meaningful until splits are supported | no |

Deleted transactions and transactions without a usable description are also excluded. Training results report how many examples each target used and how many were excluded, for each reason.

Categories are learned by their stable ID, so renaming a category in YNAB does not split its history.

## Promotion

`POST /train?plan_id=…` (Settings → Train ML Model) never overwrites the model that is serving predictions:

1. The most recent ~10% of trusted examples, by date, are held out.
2. A candidate is trained on the rest.
3. The candidate and the active model are both scored on the held-out examples. This scores the ML layer's payee and category accuracy.
4. The candidate is promoted only if it is not worse on any target the active model serves. Ties promote. With no active model, the first successfully trained model is promoted.
5. A promoted version is retrained on all trusted examples before it starts serving.
6. A rejected candidate's files are deleted. Its comparison stays in the registry.

The response and the Settings panel show the comparison and the reason.

## Versions and rollback

- `GET /models` lists versions, newest first. Each entry shows its status (`active`, `retired`, `rejected`), its training counts, exclusions and held-out comparison, and whether its files are present.
- `POST /models/{id}/activate` restores a retired version whose files still exist. The Settings panel has "Restore previous" for this.
- Files are stored in `backend/models/<version>/`, and `backend/models/ACTIVE` names the active version. The active version and the last two retired versions are kept on disk.
- Model files from before versioning are registered as version `legacy` on the next start, without retraining.
- On startup, if the active version's files are missing (for example, on Render's ephemeral disk), that same version is rebuilt from trusted examples and the rebuild is noted. It is never silently replaced.

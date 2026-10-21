# Notes

## Baseline (task 2.1), name-based classifier, before this change's model changes

- **Input:** YNAB plan export "My Budget as of 2026-10-18", 18,139 register lines.
- **Split:** default cut-off 2024-12-19.
  - Training: 11,565 rows (2017-09-24 → 2024-12-18).
  - Evaluation: 2,893 rows (2024-12-19 → 2026-10-11).
- **Treatments:**
  - 14,036 evaluated
  - 422 payee-only transfers
  - 2,142 blank description
  - 1,530 split lines
  - 9 starting balances
  - 0 uncategorized
- **Duplicates:** 338 duplicate rows kept.
- **Trained:** 1,294 payee classes and 112 category classes.

| | Payee | Category |
|---|---:|---:|
| Accuracy | 54.0% (2,893 rows) | 71.1% (2,825 rows) |
| Coverage | 100% | 100% |
| High band (≥ 0.95) | 801 rows, 90.8% | 969 rows, 95.7% |
| Medium band | 1,280 rows, 54.1% | 1,048 rows, 69.2% |
| Low band (< 0.75) | 812 rows, 17.5% | 808 rows, 44.1% |
| Exact history | 1,355 rows, 72.8% | 1,337 rows, 88.9% |
| Fuzzy history | 873 rows, 53.5% | 848 rows, 63.3% |
| ML | 665 rows, 16.4% | 640 rows, 44.2% |
| Refund subset | 190 rows, 47.9% | 190 rows, 43.7% |
| Hidden-category subset | 21 rows, 14.3% | 21 rows, 14.3% |

- **Workload:** 727 rows would be auto-accepted, with 88.9% precision; 1,537 of 2,893 rows need a correction.
- **Rules variant:** identical, because the local database has no rules.
- **Observations, not acted on in this change:**
  - Auto-accept precision is below what a 0.95 threshold suggests, mainly from payee.
  - Payee accuracy is limited even on exact history, because the same description maps to several payee names.

## After this change (task 6.2), same export, cut-off and split

| | Payee | Category |
|---|---:|---:|
| Accuracy | 54.0% → 54.0% | 71.1% → 71.0% |
| High band | 90.8% → 90.8% | 95.7% → 95.7% |
| ML source | 16.4% → 16.4% | 44.2% → 43.9% |

- Auto-accept is unchanged: 727 rows at 88.9% precision. Corrections required went from 1,537 to 1,538.
- **Why nothing moved:** the export has no renamed categories and no app-created transactions, so stable IDs and provenance filtering change nothing for this history. Training used the same 11,565 rows with no exclusions.
- **The one-row difference:** ML category labels are now IDs, which changes the class order inside the SGD model. That moves one prediction among the lowest-confidence ML rows. It is noise, not an improvement or a regression.
- **Where the benefits show:** renamed categories, approved or corrected app imports, and safe model promotion. These are covered by tests, not by this export.

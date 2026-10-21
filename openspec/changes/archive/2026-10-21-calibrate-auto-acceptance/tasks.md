# Tasks

## 1. Calibration

- [x] 1.1 Cap fuzzy historical confidence at 0.94 and require three agreeing exact examples before reaching 0.95; verify with `test_fuzzy_matches_never_reach_auto_acceptance`, `test_fewer_than_three_agreeing_examples_stay_below_auto_acceptance` and the updated `test_accepted_rows_meet_both_thresholds`
- [x] 1.2 Update the threshold notes in `AGENTS.md`, `docs/reliable-import-foundations.md` and the calibration section of `docs/classification-evaluation.md`, and verify they describe the implemented caps
- [x] 1.3 Re-run the full export evaluation and verify auto-accept precision improves with unchanged accuracy (88.9% → 92.3%, 727 → 510 rows; payee 54.0%, category 71.0%)
- [x] 1.4 Run the full backend suite and the frontend build and verify they pass (211 tests)

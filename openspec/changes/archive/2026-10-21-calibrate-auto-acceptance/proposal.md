# Proposal

## Why

The historical evaluation showed that auto-accepted rows (both targets ≥ 0.95) were right only 88.9% of the time on the YNAB export and 76.5% on the synced history, well below what a 0.95 threshold implies. Two kinds of weak historical evidence reached the threshold: fuzzy matches (wrong about 22% of the time when auto-accepted) and exact matches backed by only two agreeing examples.

## What Changes

- Fuzzy historical confidence is capped at 0.94, so a fuzzy match can suggest a payee or category but never makes a row auto-acceptable on its own.
- Exact historical confidence stays at 0.94 or below until at least three examples agree on the winning value.
- Predicted values, the 0.95 and 0.75 thresholds, rules and ML are unchanged; only review eligibility changes.
- Measured with `python -m src.evaluation` and checked on a separate period. On the 2023 validation period, auto-accepts went from 256 rows (85.9% right) to 132 rows (100% right). On Dec 2024 to Oct 2026, they went from 727 rows (88.9%) to 510 rows (92.3%).

## Capabilities

### New Capabilities

### Modified Capabilities
- `transaction-classification`: adds a requirement that weak historical evidence stays below auto-acceptance.

## Impact

- `backend/src/historical_matcher.py` (fuzzy cap, minimum agreeing examples) and its tests; `test_orchestrator.py` threshold test updated.
- Docs: `AGENTS.md` thresholds, `docs/reliable-import-foundations.md`, calibration section in `docs/classification-evaluation.md`.
- About 7% more rows go to review in recent data; wrong auto-accepts drop from 81 to 39 on that period.

# Design

## Context

See `proposal.md` for motivation and the three capability specs for behavior. The current parser returns float-based rows with no Swisscard adapter. Upload and reclassification call `bulk_predict` without account context; account selection currently happens during push. `ImportRow` stores original and edited memo separately but has no source metadata, stable identity or persisted write outcome. A new upload abandons the previous active batch.

`Transaction.amount` currently stores YNAB milliunits in a Float column; `HistoricalMatcher` compares those values directly with CSV currency units. Exact history counts winning payee/category pairs without considering conflicts. `Orchestrator` returns immediately for partial rules and aggregates ML confidence with `max`. `MLClassifier.train` already uses independently aligned target lists, with coverage in `test_ml_classifier.py`.

`WriteBackService` includes edited values and batch ID in a short digest and matches created IDs by position. `App.tsx` removes every accepted row if any creation succeeds. Existing write tests frequently mock only transaction IDs, so response fixtures must be made representative of the identity correlation contract. `transfer_detector.py` provides candidate detection and transfer payees already expose destination IDs, but neither establishes a proven repayment counterpart.

## Goals / Non-Goals

**Goals:** Establish one canonical source record and one persisted identity per occurrence; maintain exact amounts through API boundaries; make unresolved evidence and partial success visible; allow future receipt enrichment without redefining bank identity.

**Non-Goals:** Automatically resolve indistinguishable purchases, reinterpret absent legacy evidence, migrate all ML labels to category IDs, calibrate statistical accuracy, implement receipt splits or introduce background importing. Keep the explicit push workflow and current thresholds; automatic categorization does not initiate a live write.

## Decisions

### 1. Parse into an evidence envelope and explicit report

Extend the parsing service with source adapters returning a report with detected format, rows, eligible count, pending count and row errors. Each row carries raw source fields, source line/index, normalized transaction date, signed `amount_milliunits`, currency and source status. Metadata keeps Description, Merchant, Card number, both source category fields and foreign-currency fields independently; never synthesize a household owner. Normalize header whitespace/BOM for detection while retaining original values.

Swisscard requires the documented headers. Its initial signed convention is Debit with positive Amount and Credit with negative Amount; YNAB money is the negation of that exact value. Invalid signs, impossible dates, excessive precision and unknown currencies/statuses create row errors. CHF is initially supported for Swisscard, with no FX calculation; foreign-currency values are evidence only. Use standard date parsing with declared adapter formats rather than accepting partially matched dates. Validate generic and PostFinance values while preserving valid existing formats. Blank files retain an empty result; a nonempty unknown header produces a format error.

Retain Pending and invalid source envelopes in the persisted import report outside eligible review rows. Nothing in Phase 1 writes Pending records or promises automatic Pending-to-Posted matching. Detecting a new invalid upload must not abandon the active batch.

Alternative: flatten everything into the existing `source_category` and `memo` fields. Rejected because it loses evidence needed for transfer matching, later receipts and identity review.

### 2. Integer money is authoritative; legacy fields are compatibility views

Parse with `Decimal`, require exact representability in integer milliunits, and never use `int(float * 1000)`. Add authoritative integer milliunit columns to import/source records and synced transactions; use a wide integer type compatible with SQLite and PostgreSQL. Preserve legacy float fields temporarily as currency-unit display/API compatibility views on import rows and milliunit compatibility views on history. New frontend requests carry the integer amount, and the server derives write amounts from persisted rows.

Backfill transaction integer values from existing stored milliunits without multiplying by 1000; backfill import-row values from currency units with Decimal conversion. Quarantine non-finite, fractional-milliunit or otherwise invalid legacy values for resolution instead of silently rounding. Fresh YNAB sync reads integer amounts directly. Pass `Decimal(milliunits) / 1000` to prediction/rule amount boundaries so existing rule thresholds remain in currency units; historical cached amounts use that same conversion. This avoids rewriting historical amounts or silently changing rule semantics.

The [official YNAB API documentation](https://api.ynab.com/#data-formats) defines the money boundary. Validate the concrete creation response schema against the official endpoint schema while implementing the client; make no assumption that arrays of IDs have request ordering.

Alternative: change the meaning of `Transaction.amount` in place to currency units. Rejected because existing database values and sync semantics would become ambiguous.

### 3. Bind import context before classification

Require target account selection alongside upload, validate membership in the selected plan and persist it on the batch/source records. Derive account name server-side. Show detected source format and target account on review and push. Reclassification and restored batches use that persisted account. Account reassignment is an explicit operation that reruns reconciliation and classification; it cannot happen silently by changing a push dropdown.

Upload, batch retrieval, row update and write endpoints validate plan/batch/row/account relationships. Write requests reference persisted row IDs and editable overrides; bank amount, date, account scope and source identity are reloaded server-side. Old batches remain readable but must confirm account context and complete legacy reconciliation before writing. Standalone write callers without bank evidence receive a validation error directing them to create/import persisted rows; update the bundled client and tests together.

Alternative: add optional account arguments only to `predict`. Rejected because reload, account switching and write identity would still disagree.

### 4. Merge classification by target and preserve explicit flags

Extend prediction responses and persistence with payee/category confidence, source and explanation. Keep aggregate confidence as the minimum for required targets, with missing targets at zero. A rule locks only the target values it supplies. Ignore terminates classification; an explicit review flag remains set regardless of later scores. Fill remaining targets using exact history, eligible fuzzy history, then ML, leaving missing values unclassified. Each target's explanation identifies its evidence. A fully resolved transfer uses separate routing review rules instead of requiring an ordinary category.

Keep historical candidates indexed with account, signed amount and outcomes. Exact lookup first uses same-account, same-sign candidates; if unavailable, expose broader same-sign evidence as a contextual fallback and cap it below auto-acceptance. For each target, confidence is winning-label share multiplied by `min(1, 0.85 + 0.05 * winning_count)`, counting all nonmissing competing labels in that contextual set. Ties require review with deterministic ordering. This keeps repeated consistent evidence useful while a 3:2 conflict yields 0.60 rather than 1.0. Record amount similarity for disambiguation/explanation; do not invent recency weights without evaluation. Fuzzy scoring retains its existing 0.75 eligibility threshold with normalized amounts and no reward for two missing account IDs.

Auto-accept non-transfer rows only when both required outputs exist, both target confidences are at least 0.95, and no explicit or reconciliation review flag applies. These are operational scores, not measured accuracy. Preserve the existing independent ML training pairs and regression tests; do not load bundled joblib artifacts for review or retrain production models during verification.

Alternative: retain one confidence and lower `max` to `min` everywhere. Insufficient because rules and history can provide only one target and have different provenance.

### 5. Persist source occurrences separately from upload batches

Add a source-record ledger with plan/account scope, versioned canonical fingerprint, occurrence ordinal, immutable raw evidence, stable source ID, YNAB import ID, reconciliation state and known YNAB transaction ID. A unique database constraint covers scope/fingerprint/occurrence; uploads reference those records through import rows. Keep import review state distinct from ledger write state.

The fingerprint uses source format, canonical source date, exact signed amount, currency, masked card identity when supplied, raw-description text with only declared transport normalization, and raw merchant. Exclude predictions, outgoing memo, upload IDs, Pending/Posted state and editable source category labels. Keep raw evidence alongside the hash; compare it when resolving a collision. Prefer a real bank transaction ID when a future adapter provides one. Generate a versioned, sufficiently long digest-based YNAB import ID within the API's verified length restrictions and persist it once.

Treat an export as a multiset: assign separate stable occurrence ordinals to identical eligible rows. Persist an export-content signature independent of row order. Exact repeated snapshots reuse the same occurrence set. For overlapping exports, reuse ledger occurrences when one-to-one evidence is clear; allocate additional occurrences only when the new export demonstrates a greater simultaneous multiplicity or the user confirms a new purchase. A partial overlap with indistinguishable multiplicity is a conflict, not grounds to allocate another ID. Transactionally allocate/reserve identities so simultaneous uploads cannot invent extra occurrences. A repeated source ID cannot be sent twice in the same write operation.

Before writes, refresh relevant account history through the existing sync/client read path. Extend synced metadata with import ID and transfer linkage where supplied. Match known ledger links first; otherwise compare account, date, exact signed amount and bank-description/merchant evidence. Count candidate multiplicity. A unique, sufficiently evidenced legacy match becomes an explicit persisted link; equal amount/date alone is only a potential match and blocks that candidate pending confirmation. If history cannot be refreshed, leave unverified legacy matches unresolved. A deleted linked record requires explicit resolution rather than automatic recreation. Reassignment to another account creates a new scope only after checking existing links.

Alternative: hash date/amount/memo with an in-memory occurrence counter. Rejected because it cannot retain multiplicity across uploads, handle prior external imports or recover uncertain network outcomes.

### 6. Handle repayments through explicit transfer evidence

Extend the existing transfer candidate detector with Swisscard repayment descriptions, keeping candidate detection separate from confirmed routing. Set no ordinary category on repayment candidates. Use available Post history and YNAB transfer/payee linkage to propose a same-plan source account and existing counterpart; require confirmation for unresolved proposals. Never infer source routing from household card ownership.

If a Post-side transfer already produced the matching card-side record, link that record and skip creation. For a confirmed new transfer with no existing counterpart, resolve the transfer payee ID and write only one side with null category after the refreshed reconciliation check. Unknown or multiple counterpart candidates stay blocked with an actionable explanation. Refund credits remain ordinary refund candidates rather than being treated as repayments merely because their amount is positive; no automatic original-purchase linking is promised here.

### 7. Persist outcomes and reconcile uncertain requests

Return row ID, source ID, import ID, status, error and YNAB transaction ID for each submitted row. Correlate returned transaction objects by import ID and duplicates by exact duplicate import ID. If only transaction IDs are available, read those transactions to recover explicit associations; never zip ID arrays to payload order. Missing, repeated or contradictory associations become unresolved. HTTP errors and timeouts do not prove that nothing was created: reconcile history/import IDs before resending the same persisted IDs.

Persist a short in-flight reservation before the external call and finalize verified outcomes afterward; do not hold a database transaction across the network. A uniqueness/claim guard prevents simultaneous writes for one source record. On timeout or process restart, transition the reservation to reconciliation-required. The same persisted import ID makes retries idempotent within the app's identity scheme. Confirmed duplicate responses may complete a row even when no YNAB ID was returned, while retaining that limitation for subsequent read reconciliation.

Track write state separately from accepted/pending/ignored review state. Only confirmed created or confirmed existing/skipped rows leave the active queue, including duplicate-only responses. Errors and unresolved rows remain with edits. Reload and reclassification exclude completed ledger entries, and the backend rejects premature batch completion. Dry run executes the same local validation/reconciliation checks without sending writes, claiming source records or marking completion. All creates explicitly send `approved=false`.

Alternative: remove accepted UI rows after any successful request. Rejected because local acceptance is not evidence of a successful external write.

## Risks / Trade-offs

- CSV has no stable bank transaction ID -> ambiguity is surfaced; occurrence logic cannot guarantee automatic reconciliation of every overlapping subset.
- Legacy records lack immutable metadata and old IDs include edited values -> preserve old batches, require explicit links where needed, never infer certainty from a new hash.
- Additional history reads increase latency -> reuse the existing delta-sync path and constrain reconciliation by account/date while preserving evidence completeness.
- SQLite/PostgreSQL differ in migration and concurrency behavior -> use additive Alembic migrations, portable constraints and explicit claim handling; validate on disposable databases.
- Conservative confidence and transfer handling can increase review -> explain the reason and measure future improvements in Phase 2 rather than relaxing thresholds without evidence.
- Requiring persisted row references changes direct API callers -> update the bundled frontend, document the new contract, and return clear errors for legacy requests.

## Migration Plan

1. Establish backend/full-build baseline using the local environment and isolated database/model paths. Preserve existing staged handoff files and private ignored data.
2. Add nullable canonical money/context/evidence fields, source ledger and result/claim metadata through Alembic. Backfill representable money values without changing legacy column meanings. Preserve legacy rows with explicit unresolved metadata where evidence is missing.
3. Deploy the backend and bundled frontend together. Existing batches load in compatibility mode, with account confirmation and reconciliation required before their first new write. New imports use canonical records immediately.
4. Verify fresh-install and existing-schema upgrades with synthetic data in disposable SQLite; validate PostgreSQL-compatible types and constraints against a disposable PostgreSQL instance when available, documenting any unverified runtime coverage. Never migrate the live budget or local production database as a development check.
5. Roll back application behavior by disabling writes and restoring the previous release. Preserve the additive ledger/evidence tables until recovery is reviewed; a database downgrade requires a backup because removing the ledger removes deduplication evidence. Do not claim external YNAB writes can be undone by a schema rollback.

## Planning Baseline

On 19 October 2026, the current checkout passed all 87 backend tests (115 existing deprecation warnings) and the frontend production build with the documented placeholder hosted settings. The backend suite ran through `pytest.main(['tests/', '-v', '-o', 'faulthandler_timeout=30'])` using the local virtual environment, an in-memory default database and temporary model paths to avoid loading local model artifacts. A sandboxed attempt stalled at the TestClient HTTP test; the approved rerun outside the sandbox completed in 1.96 seconds. This baseline does not measure classification accuracy or validate the proposed Phase 1 behavior, private samples or live service connectivity.

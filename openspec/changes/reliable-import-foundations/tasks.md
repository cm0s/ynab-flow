# Tasks

## 1. Establish the implementation baseline

- [x] 1.1 Confirm the planning baseline recorded in `design.md` (87 backend tests and frontend build passed); if the checkout or environment has changed, rerun `pytest tests/ -v` with isolated database/model paths and `pnpm run build` with the documented placeholder settings. Verify the baseline note is current without live YNAB or mailbox calls.
- [x] 1.2 Confirm the available ignored reference files and inspect only format metadata needed for adapters; deliver an availability note and synthetic fixtures, keeping private inputs out of tracked files and logs.

## 2. Canonical money, evidence and persistence

- [x] 2.1 Add Decimal-to-integer-milliunit conversion and declared date validation helpers; verify exact debit/refund/repayment values, precision limits, non-finite rejection and impossible dates in focused tests.
- [x] 2.2 Add additive Alembic migrations and models for canonical money, persisted account context, import report/source evidence, source-occurrence ledger, per-target confidence and write/link/claim state; verify fresh schema creation and upgrade from the prior migration on disposable SQLite.
- [x] 2.3 Backfill legacy history milliunits and import currency-unit amounts without changing old column semantics, leaving missing evidence/account links unresolved; verify synthetic old rows, invalid values and readable legacy batches in migration tests.
- [x] 2.4 Document new fields, legacy compatibility, SQLite/PostgreSQL considerations and rollback limitations; verify documentation against the migration and model contracts and record whether disposable PostgreSQL runtime validation is available.

## 3. CSV adapters and account context

- [x] 3.1 Implement header-based Swisscard detection and evidence envelopes in `csv_service.py` while retaining valid generic and PostFinance behavior; verify quoted UTF-8/BOM inputs, reordered headers, blank Merchant credits and unsupported layouts using synthetic parser tests.
- [x] 3.2 Implement Swisscard sign/date/currency/status validation with Posted default, stored Pending evidence and separate detected/eligible/pending/invalid counts; verify a synthetic 174-row export yields 167 Posted candidates, 7 Pending exclusions and all 3 credits retained, plus row-level failures for malformed inputs.
- [x] 3.3 Extend upload, serialization, reload and reclassification contracts in `main.py` to persist target account and source metadata; verify account-scoped prediction, plan/account mismatch rejection and failed upload preserving the active batch in API tests.
- [x] 3.4 Add pre-upload account selection, source/target labels, import counts and actionable parser errors to the frontend/API types; verify selection and metadata after upload/reload with the build and a local mocked workflow, and document supported formats and changed upload requirements.

## 4. Prediction reliability

- [x] 4.1 Store synced integer amounts and normalize history/prediction amount features to currency units without changing rule thresholds; verify CHF -50.00 CSV and -50000 history yield equal amount treatment and missing account IDs receive no agreement bonus.
- [x] 4.2 Extend historical indexes with account/sign context and implement conflict-aware per-target confidence with deterministic ties and capped contextual fallback; verify 3:2 category conflicts remain below auto-acceptance and account/sign differences affect evidence selection.
- [x] 4.3 Merge rule/history/ML outputs by target, retaining rule assignments and review/ignore actions; verify payee-only rules allow category inference, category-only rules preserve their category, and explicit ignore/review cannot be cleared by later evidence.
- [x] 4.4 Persist/expose payee and category confidence/provenance and minimum aggregate confidence; verify 0.99 payee plus 0.20 category requires review, missing categories block auto-acceptance, fuzzy below 0.75 falls through, and accepted rows meet both 0.95 thresholds.
- [x] 4.5 Preserve the existing independent ML training pairs and original evidence/user overrides; run the existing alignment regression and reclassification API tests, add coverage only for missing cases, and document scores as confidence rather than measured accuracy.

## 5. Stable identity and reconciliation

- [x] 5.1 Implement versioned canonical fingerprints, raw-evidence comparison, persisted import IDs and occurrence allocation under database uniqueness constraints; verify edits/new batches do not change identity and two identical purchases remain distinct.
- [x] 5.2 Implement export multiset signatures and occurrence reconciliation for repeated/reordered files and overlapping windows; verify repeated files allocate no extra identities, increased demonstrated multiplicity adds only the extra occurrences and ambiguous partial subsets require resolution.
- [x] 5.3 Extend sync/client metadata with import IDs and transfer linkage, and reconcile relevant refreshed YNAB history before writes; verify explicit ledger links, unambiguous legacy matches, same-date/amount conflicts, deleted links and unavailable refresh all produce the specified outcomes using mocked reads.
- [x] 5.4 Add row-level reconciliation resolution and explicit account reassignment across API/frontend; verify users can link an existing record or confirm a distinct occurrence without overriding immutable bank values, and reassignment rechecks identity scope and history.
- [x] 5.5 Guard simultaneous identity allocation, duplicate row submissions and account/plan ownership; verify database conflict handling creates one occurrence set and cross-plan or forged source references cannot enter the write queue.
- [x] 5.6 Document occurrence limits, legacy external-import matching and deleted-record resolution; verify examples match the synthetic overlap/conflict cases and make no guarantee of automatic deduplication for indistinguishable subsets.

## 6. Repayment safeguards

- [x] 6.1 Extend transfer detection for Swisscard repayment descriptions without classifying every positive credit as a transfer; verify blank-merchant repayments stay visible and a merchant refund remains distinct in synthetic tests.
- [x] 6.2 Implement transfer proposal/confirmation using valid same-plan accounts, transfer payee IDs and refreshed counterpart linkage; verify unresolved/multiple counterparts block writes, an existing Post-side transfer links its card-side counterpart, and confirmed new transfers write only one side with null category.
- [x] 6.3 Add visible repayment routing state and resolution actions to review, retaining exact bank values; verify the mocked UI can resolve a valid account or keep an unresolved repayment for later, and document that no card-owner or lunch policy is inferred.

## 7. Write outcomes, retries and audit state

- [x] 7.1 Change write requests to reference persisted import rows, validate scope and read immutable values server-side; verify legacy standalone payloads receive an actionable error, forged amounts/account context are rejected or ignored in favor of persisted evidence, and all created payloads explicitly use `approved=false`.
- [x] 7.2 Validate creation response/import-ID constraints against official YNAB endpoint documentation and update client fixtures; correlate transaction objects and duplicates by identity, resolving ID-only responses via mocked transaction reads, and verify reordered mixed outcomes and missing/conflicting mappings never create positional associations.
- [x] 7.3 Implement short persisted write claims and uncertain-outcome recovery without holding database transactions over network calls; verify simultaneous pushes cannot send the same source twice, timeout/restart triggers reconciliation, and retries reuse the same import IDs.
- [x] 7.4 Persist created/existing/error/unresolved outcomes separately from review acceptance and expose them in batch/result endpoints; verify mixed and duplicate-only outcomes survive reload, failed edits remain and premature batch completion is rejected.
- [x] 7.5 Update `App.tsx` and review/push components to remove only confirmed handled source rows and retain failures/unresolved rows; verify mixed success, duplicate-only success, retry and reload through a mocked local workflow, including dry runs retaining all review rows.
- [x] 7.6 Document new row-reference writes, per-row result fields, dry-run behavior and the distinction between local acceptance and YNAB approval; verify documented request/response examples against API tests.

## 8. Integrated Phase 1 acceptance

- [x] 8.1 Exercise upload -> prediction -> edit -> dry run -> mocked mixed write -> reload -> retry -> overlapping re-upload using isolated state; verify exact money, immutable evidence, stable identities, repayment holds and row-level results across the whole workflow.
- [x] 8.2 If ignored original samples are available, perform read-only parsing and dry-run review, reporting actual detected/Posted/Pending/credit counts against the handoff expectations; otherwise explicitly record unavailable private-sample validation without substituting synthetic results as measurements.
- [x] 8.3 Run the full backend suite `pytest tests/ -v` and frontend production build `pnpm run build` with documented placeholder hosted values; report results, migration coverage, any pre-existing failures and remaining limitations, then propose a commit and wait for approval.

## Workflow follow-up

- Review and archive the change only after implementation and required validation are complete.
- Plan Phase 2 evaluation separately before claiming prediction accuracy; Proton/cloud AI remain later changes with their own setup decisions.

# YNAB Transaction Automation Application — Detailed Specification

Version: 1.0  
Date: 2026-03-09  
Target audience: AI coding agents, software engineers, future maintainers  
Document purpose: provide a complete functional and technical specification for building a local-first application that automates monthly transaction classification and posting to YNAB.

---

## 1. Executive summary

The application will automate the monthly workflow of importing bank transactions into YNAB.

Current process:
1. Download a CSV from the bank for the previous month.
2. Run an existing conversion script that transforms the bank CSV into a YNAB-compatible import CSV.
3. Import transactions into YNAB.
4. Manually assign Payee and Category for many transactions.

Pain point:
- The manual classification step is repetitive and time-consuming.
- The user already has nearly 10 years of YNAB history that can be reused as labeled data.

Target solution:
- Build a local-first web application that ingests the monthly converted CSV, syncs historical YNAB transactions, learns from prior assignments, predicts the best Payee and Category for each new transaction, lets the user review only uncertain items, and writes the final result to YNAB through the official API.

Core principle:
- Do not start with an LLM-first design.
- Use a hybrid pipeline: deterministic rules + historical matching + lightweight ML classifier + human review + continuous feedback.

---

## 2. Product vision

Create a private, reliable, explainable personal-finance assistant for YNAB that:
- runs locally by default,
- minimizes manual categorization work,
- improves over time from user corrections,
- avoids fragile browser automation,
- uses the YNAB API as the system of record for plans, accounts, payees, categories, and transactions.

---

## 3. Goals

### 3.1 Primary goals
- Reduce monthly manual work for YNAB import by at least 80% after training.
- Automatically infer canonical Payee names from noisy bank transaction labels.
- Automatically infer YNAB Category for known and recurring transactions.
- Provide confidence-based review so only uncertain transactions require manual validation.
- Maintain a full audit trail of predictions, corrections, and sync runs.

### 3.2 Secondary goals
- Continuously improve accuracy from corrections.
- Support merchant aliasing and normalization rules.
- Support direct write-back to YNAB via API.
- Support export to YNAB CSV if API write mode is disabled.
- Offer explainable suggestions such as “matched previous merchant”, “rule matched”, or “high-similarity historical memo”.

### 3.3 Success metrics
- Payee top-1 accuracy >= 95% on recurring merchants after training.
- Category top-1 accuracy >= 90% on recurring merchants after training.
- At least 70% of monthly imported transactions processed without manual edits after three months of usage.
- Review queue contains only low/medium-confidence items.

---

## 4. Non-goals

The first release will not attempt to:
- replace YNAB budgeting logic,
- generate budget recommendations,
- infer split transactions from a single bank line with high complexity,
- connect directly to bank APIs,
- serve multiple unrelated users as a SaaS product,
- automate browser clicks in the YNAB UI,
- become a general accounting platform.

Optional later features:
- split-transaction suggestion,
- scheduled recurring transaction prediction,
- receipt OCR,
- multi-user or multi-tenant support,
- hosted deployment,
- multilingual merchant enrichment via external services.

---

## 5. Users and personas

### 5.1 Primary user
A technically capable individual user who:
- already uses YNAB regularly,
- imports transactions monthly,
- has a long transaction history in YNAB,
- values local privacy and control,
- wants a robust tool rather than a no-code hack.

### 5.2 Secondary user
A future advanced user who may want:
- multiple YNAB plans,
- multiple accounts,
- configuration per account,
- reusable category mapping logic.

---

## 6. Current state and input assumptions

### 6.1 Existing workflow assumptions
- The user already has a script that converts the bank CSV into a YNAB-compatible CSV-like structure.
- The user downloads a new bank CSV once per month.
- The user already has nearly 10 years of YNAB historical data.

### 6.2 Sample imported CSV structure observed
The provided sample file contains these columns:
- `Date`
- `Memo`
- `Inflow`
- `Outflow`
- `Label`
- `Catégorie`

Observations from the sample:
- `Memo` contains noisy raw bank labels.
- `Payee` is not present in the sample.
- `Catégorie` appears to be a custom source column useful as a training signal or fallback metadata.
- Categories in the sample are human-friendly labels such as `Mobilité // Transports publics`, `Finances // Assurances`, etc.
- The sample includes many recurring patterns such as TWINT merchants, phone provider payments, insurance payments, tax payments, transport purchases, and transfers.

### 6.3 Important implication
The application should treat the converted CSV as an internal input format, not as the final import artifact. The system should enrich each row with:
- canonical payee,
- predicted YNAB category,
- confidence,
- explanation,
- review decision,
- optional write-back metadata.

---

## 7. Product scope

The application must support two end-to-end operating modes.

### 7.1 Mode A — Review then write to YNAB API (preferred)
1. Import monthly CSV.
2. Predict payee/category.
3. User reviews uncertain items.
4. Approved transactions are created or updated in YNAB through API.

### 7.2 Mode B — Review then export enriched CSV (fallback)
1. Import monthly CSV.
2. Predict payee/category.
3. User reviews uncertain items.
4. Application exports:
   - a YNAB import CSV for transactions,
   - a review report,
   - optionally a mapping file.

Mode A is the default and main target.

---

## 8. Recommended architecture

### 8.1 Architecture style
Local-first monorepo with:
- Backend API and ML pipeline
- Frontend web UI
- Local database
- Background job runner inside backend process

### 8.2 Recommended stack

#### Frontend
- React
- TypeScript
- Vite
- TanStack Query
- Zustand or simple context state for UI state
- Tailwind CSS
- shadcn/ui or equivalent component library

#### Backend
- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy 2.x
- Alembic
- httpx or requests
- pandas
- rapidfuzz
- scikit-learn
- joblib

#### Database
- SQLite for local-first MVP
- PostgreSQL support as optional future upgrade

#### Packaging / tooling
- Backend dependency management with `uv` or Poetry
- Frontend dependency management with `pnpm`
- Docker Compose for optional containerized local run
- Ruff + mypy + pytest for backend quality
- ESLint + Prettier + Vitest + Playwright for frontend quality

### 8.3 Why this stack
- Python is ideal for CSV processing, normalization, similarity, ML, and API orchestration.
- FastAPI is lightweight and easy for AI coding agents to generate.
- React + TypeScript provides an understandable review UI.
- SQLite is sufficient for a personal local app and simplifies deployment.

---

## 9. High-level system components

### 9.1 Frontend
Responsibilities:
- file upload,
- sync status display,
- transaction review table,
- rule management UI,
- settings,
- metrics dashboard,
- sync history.

### 9.2 Backend API
Responsibilities:
- expose REST endpoints,
- parse imports,
- run prediction pipeline,
- store results,
- manage YNAB synchronization,
- train/update models,
- record corrections.

### 9.3 Classification engine
Responsibilities:
- normalize raw labels,
- apply deterministic rules,
- retrieve similar historical transactions,
- run ML models,
- compute final confidence and explanation.

### 9.4 Persistence layer
Responsibilities:
- store raw imports,
- store normalized forms,
- store rules,
- cache YNAB categories/payees/accounts,
- store predictions and corrections,
- store sync state and model versions.

### 9.5 YNAB integration adapter
Responsibilities:
- authenticate using personal access token,
- fetch plans/accounts/categories/payees/history,
- create transactions,
- optionally patch transactions,
- handle retries and rate limits,
- maintain remote-to-local mapping.

---

## 10. Functional requirements

## FR-1. CSV import

The application shall:
- accept drag-and-drop or file picker upload of a monthly CSV file,
- support UTF-8 CSV input,
- infer delimiter if possible, with manual override,
- validate required input columns,
- show a preview of parsed rows,
- store the original file unchanged for audit/debug,
- reject files with invalid date/amount schema unless user maps columns manually.

### FR-1.1 Required input mapping
The import layer must support column mapping for at least:
- transaction date,
- raw memo/description,
- inflow amount,
- outflow amount,
- source category (optional),
- source label/tag (optional),
- source account (optional if batch is bound to one account).

### FR-1.2 Account assignment
The import file may be associated with one YNAB account chosen by the user.

---

## FR-2. Historical YNAB sync

The application shall be able to import historical YNAB data for training and cache refresh.

### FR-2.1 Sync scope
The system must fetch and cache at least:
- plans,
- accounts,
- categories,
- payees,
- transactions,
- optionally scheduled transactions.

### FR-2.2 Sync modes
- Initial full sync
- Incremental sync using YNAB delta request support where applicable
- Manual resync

### FR-2.3 Metadata caching
The app must cache:
- category ID + display name,
- payee ID + display name,
- account ID + display name,
- plan metadata,
- last sync knowledge/version state.

---

## FR-3. Transaction normalization

The application shall transform noisy raw bank memos into stable merchant-like tokens.

### FR-3.1 Normalization rules
Normalization must include support for:
- uppercase/lowercase normalization,
- Unicode normalization and accent handling,
- whitespace collapsing,
- punctuation cleanup,
- stripping dates embedded in memo text,
- stripping IBANs and long account numbers,
- stripping postal codes and cities where useful,
- stripping generic payment boilerplate such as `ACHAT/PRESTATION`, `DÉBIT`, `TWINT DU`, etc.,
- configurable stopwords per bank/account/language.

### FR-3.2 Merchant stem extraction
The system shall attempt to derive a merchant stem from the memo, for example:
- `ACHAT/PRESTATION TWINT ... SBB MOBILE BERN (CH)` -> `SBB MOBILE`
- `DÉBIT ... SUNRISE GMBH / YALLO ...` -> `SUNRISE / YALLO`
- `DÉBIT ... ETAT DE VAUD IMPÔTS ...` -> `ETAT DE VAUD IMPOTS`

### FR-3.3 Explainability
For each normalized transaction, the backend must store:
- original memo,
- cleaned memo,
- merchant stem,
- matched rule name if any.

---

## FR-4. Rule engine

The application shall support deterministic payee/category assignment rules.

### FR-4.1 Rule types
Support at minimum:
- exact match on normalized memo,
- substring contains,
- regex match,
- amount sign filter,
- amount range filter,
- account filter,
- inflow/outflow filter,
- source category filter,
- source label filter.

### FR-4.2 Rule outputs
A rule may assign:
- canonical payee,
- category,
- both payee and category,
- review-required flag,
- ignore/exclude flag.

### FR-4.3 Rule priority
Rules must have explicit priority. Higher-priority rules win.

### FR-4.4 Rule provenance
The UI must show which rule matched a transaction.

---

## FR-5. Historical matching engine

The application shall reuse prior classified transactions before invoking ML.

### FR-5.1 Exact historical lookup
If a normalized transaction signature exactly matches a prior known signature, the application should reuse the most likely prior payee/category.

### FR-5.2 Fuzzy similarity lookup
For near matches, the system must search historical data using approximate string similarity.

### FR-5.3 Similarity features
At minimum use:
- normalized memo similarity,
- merchant stem similarity,
- amount sign,
- amount bucket,
- account,
- transaction direction.

### FR-5.4 Historical decision policy
Use the most common prior payee/category among sufficiently similar historical matches, weighted by recency and frequency.

---

## FR-6. ML prediction engine

The application shall predict payee and category when deterministic rules and strong historical matching do not fully resolve the transaction.

### FR-6.1 Model approach
Use lightweight classical ML first.

Recommended baseline:
- Payee model: multi-class classifier
- Category model: multi-class classifier

Recommended features:
- character n-grams over normalized memo,
- word n-grams over normalized memo,
- merchant stem tokens,
- inflow/outflow direction,
- signed amount,
- amount bucket/log amount,
- account ID,
- optional source category.

Recommended baseline models:
- TF-IDF + Logistic Regression
- or TF-IDF + Linear SVM with probability calibration
- or LightGBM/CatBoost if later needed

### FR-6.2 Confidence score
Each prediction must produce:
- predicted payee,
- predicted category,
- confidence score in [0,1],
- top-N alternatives,
- human-readable explanation.

### FR-6.3 Confidence policy
Initial thresholds:
- >= 0.95 auto-approve
- 0.75–0.95 review recommended
- < 0.75 manual review required

Thresholds must be configurable.

### FR-6.4 Training schedule
- train on first bootstrap from YNAB history,
- retrain after a configurable number of corrections,
- allow manual retraining from settings.

### FR-6.5 Model versioning
Store model metadata:
- model version,
- training timestamp,
- training row count,
- feature schema hash,
- evaluation metrics.

---

## FR-7. Prediction orchestration

Prediction flow for each transaction must be:
1. Parse and validate
2. Normalize memo
3. Check deduplication / existing processing status
4. Apply deterministic rules
5. Perform exact historical match
6. Perform fuzzy historical match
7. Run ML payee/category prediction if needed
8. Combine results with confidence
9. Decide auto-approve vs review
10. Persist prediction and explanation

### FR-7.1 Decision precedence
Recommended precedence:
1. Manual override
2. Explicit rule
3. Exact historical reuse
4. High-confidence fuzzy historical reuse
5. ML prediction
6. Blank/unclassified

---

## FR-8. Review workflow

The system shall provide a review interface for non-auto-approved transactions.

### FR-8.1 Review table columns
At minimum show:
- date,
- raw memo,
- normalized memo,
- amount,
- predicted payee,
- predicted category,
- confidence,
- explanation,
- source category,
- source label,
- account,
- duplicate flag,
- approval state.

### FR-8.2 Review actions
User must be able to:
- accept prediction,
- change payee,
- change category,
- mark as transfer,
- ignore transaction,
- create new rule from correction,
- bulk-apply same decision to similar transactions.

### FR-8.3 Bulk review
Must support multi-select and bulk actions.

### FR-8.4 Search and filters
Must support filters by:
- confidence,
- account,
- amount range,
- source category,
- predicted category,
- review state,
- date range,
- merchant stem.

---

## FR-9. YNAB write-back

The application shall support writing approved transactions to YNAB via API.

### FR-9.1 Write modes
Support:
- dry-run validation,
- create-only,
- create-or-skip-if-duplicate,
- optional patch/update mode for previously created transactions managed by this app.

### FR-9.2 Data written to YNAB
For each approved transaction the system should send or derive:
- plan ID,
- account ID,
- date,
- amount,
- payee or payee name,
- category or category name,
- memo,
- cleared state if configured,
- import/dedup metadata if supported by the current API schema/SDK.

### FR-9.3 Safe write policy
Before write-back:
- verify plan/account/category/payee references exist,
- confirm amount sign conversion,
- verify no known duplicate fingerprint locally,
- optionally query YNAB recent transactions to reduce duplicate risk.

### FR-9.4 Reconciliation result
After write-back, record:
- local transaction ID,
- remote YNAB transaction ID if returned,
- status,
- error message if any,
- write timestamp.

---

## FR-10. CSV export fallback

The application shall be able to export processed transactions if API mode is disabled.

### FR-10.1 Export artifacts
Generate:
- YNAB-compatible import CSV,
- review summary CSV,
- unresolved-transactions CSV,
- optional audit JSON.

### FR-10.2 Export purpose
This mode exists as a fallback, not the preferred primary mode.

---

## FR-11. Transfer handling

The application shall support special handling for transfers.

### FR-11.1 Transfer detection heuristics
Potential signals:
- source category indicates transfer,
- historical category/payee indicates transfer,
- memo contains account transfer patterns,
- matching opposite-signed transaction exists in another account around same date/amount.

### FR-11.2 Transfer review
Because false positives are risky, transfer detection should default to review unless confidence is very high.

---

## FR-12. Feedback loop and learning

Every user correction must become training data.

### FR-12.1 Correction capture
When user changes a suggestion, store:
- raw memo,
- normalized memo,
- amount,
- account,
- predicted payee/category,
- final payee/category,
- model version,
- explanation source,
- timestamp.

### FR-12.2 Learning actions
User may choose one or more:
- “save correction only”
- “create exact rule”
- “create regex/contains rule”
- “retrain later”

---

## FR-13. Configuration and admin

The application shall provide a settings area.

### FR-13.1 Settings categories
- YNAB API token
- selected plan
- account mapping defaults
- confidence thresholds
- auto-approval toggles
- normalization stopwords
- model retraining behavior
- write mode
- data retention policy

### FR-13.2 Secrets handling
Secrets must never be stored in plaintext inside frontend storage.

---

## FR-14. Metrics and observability

The application shall provide operational metrics.

### FR-14.1 User-facing metrics
Display:
- auto-approved count,
- review-required count,
- average confidence,
- per-category accuracy,
- payee accuracy,
- number of created rules,
- training set size,
- sync duration.

### FR-14.2 Technical logs
Backend logs must include:
- import run ID,
- sync run ID,
- model version,
- transaction processing timing,
- YNAB API errors,
- retraining summary.

---

## 11. Non-functional requirements

### 11.1 Privacy
- Local-first by default.
- No transaction data sent to third-party AI services unless explicitly enabled in a future optional feature.
- Support fully offline review after sync/import.

### 11.2 Reliability
- Imports must be idempotent.
- Duplicate transaction writes must be minimized.
- Partial failure must not corrupt previous state.

### 11.3 Explainability
- Every prediction must expose why it was suggested.
- Rule and historical reuse should always be preferred over opaque inference when equally good.

### 11.4 Performance
Target for a personal dataset of 10 years and monthly imports:
- initial CSV parse under 3 seconds for typical monthly file,
- prediction for a monthly batch under 10 seconds on a standard laptop,
- review UI page load under 2 seconds for 500 rows.

### 11.5 Maintainability
- Strong typing where practical
- Clear layering
- No hidden business logic in frontend
- Comprehensive test suite

### 11.6 Portability
Must run on:
- Linux
- macOS
- Windows via Docker or native runtime

---

## 12. UI/UX specification

## 12.1 Main screens

### A. Onboarding / setup
- Enter YNAB token
- Test connection
- Select plan
- Select one or more accounts
- Initial historical sync
- Optional first model training

### B. Import screen
- Upload CSV
- Preview mapped columns
- Assign target account
- Start classification run

### C. Review screen
- Main transaction grid
- Filters sidebar
- Detail drawer with explanation and similar history
- Approve/edit/bulk actions

### D. Rules screen
- List rules by priority
- Create/edit/test rule
- Show sample matches

### E. Sync history screen
- Initial and incremental sync runs
- Result counts
- Errors
- Duration

### F. Model screen
- Current model version
- Last training time
- Training set size
- Evaluation metrics
- Retrain button

### G. Settings screen
- API settings
- thresholds
- data directory
- backup/export

## 12.2 Review UX requirements
- Keyboard-friendly workflow
- Fast autocomplete for payees and categories
- Top suggestions visible without opening dialogs
- Visual confidence indicators
- Explanation shown inline or in tooltip
- Undo support for recent edits

## 12.3 Accessibility
- Proper labels
- Keyboard navigation
- Sufficient contrast
- Focus states

---

## 13. Data model

Below is the recommended logical schema. Exact SQL schema can differ slightly.

### 13.1 `plans`
- `id` (local UUID)
- `ynab_plan_id`
- `name`
- `is_default`
- `last_server_knowledge`
- `created_at`
- `updated_at`

### 13.2 `accounts`
- `id`
- `plan_id`
- `ynab_account_id`
- `name`
- `type`
- `closed`
- `currency`
- `created_at`
- `updated_at`

### 13.3 `categories`
- `id`
- `plan_id`
- `ynab_category_id`
- `group_name`
- `name`
- `full_name`
- `hidden`
- `deleted`
- `created_at`
- `updated_at`

### 13.4 `payees`
- `id`
- `plan_id`
- `ynab_payee_id`
- `name`
- `transfer_account_id` (nullable)
- `deleted`
- `created_at`
- `updated_at`

### 13.5 `raw_import_files`
- `id`
- `filename`
- `sha256`
- `account_id`
- `imported_at`
- `row_count`
- `original_path`
- `status`

### 13.6 `import_transactions`
- `id`
- `raw_import_file_id`
- `row_index`
- `date`
- `raw_memo`
- `inflow`
- `outflow`
- `signed_amount`
- `source_label`
- `source_category`
- `source_account_name`
- `fingerprint`
- `created_at`

### 13.7 `normalized_transactions`
- `id`
- `import_transaction_id`
- `cleaned_memo`
- `merchant_stem`
- `normalization_version`
- `normalization_metadata_json`
- `created_at`

### 13.8 `historical_transactions`
- `id`
- `plan_id`
- `account_id`
- `ynab_transaction_id`
- `date`
- `amount_milliunits`
- `amount_display`
- `payee_id`
- `payee_name`
- `category_id`
- `category_name`
- `memo`
- `cleared`
- `approved`
- `deleted`
- `imported_from_ynab_at`
- `fingerprint`

### 13.9 `prediction_runs`
- `id`
- `raw_import_file_id`
- `model_version`
- `started_at`
- `finished_at`
- `status`
- `summary_json`

### 13.10 `predictions`
- `id`
- `prediction_run_id`
- `import_transaction_id`
- `predicted_payee_id`
- `predicted_payee_name`
- `predicted_category_id`
- `predicted_category_name`
- `payee_confidence`
- `category_confidence`
- `overall_confidence`
- `decision_source` (rule | exact_history | fuzzy_history | ml | manual)
- `explanation`
- `alternatives_json`
- `requires_review`
- `approved`
- `written_to_ynab`
- `created_at`
- `updated_at`

### 13.11 `manual_corrections`
- `id`
- `prediction_id`
- `final_payee_id`
- `final_payee_name`
- `final_category_id`
- `final_category_name`
- `create_rule_requested`
- `user_note`
- `created_at`

### 13.12 `rules`
- `id`
- `name`
- `priority`
- `enabled`
- `match_type`
- `pattern`
- `account_filter`
- `direction_filter`
- `amount_min`
- `amount_max`
- `source_category_filter`
- `source_label_filter`
- `assign_payee_id`
- `assign_payee_name`
- `assign_category_id`
- `assign_category_name`
- `force_review`
- `ignore_transaction`
- `created_at`
- `updated_at`

### 13.13 `sync_runs`
- `id`
- `plan_id`
- `sync_type` (full | incremental)
- `started_at`
- `finished_at`
- `status`
- `server_knowledge_before`
- `server_knowledge_after`
- `summary_json`
- `error_json`

### 13.14 `model_registry`
- `id`
- `model_name`
- `version`
- `artifact_path`
- `feature_schema_json`
- `train_row_count`
- `metrics_json`
- `created_at`
- `active`

---

## 14. API design (backend)

All endpoints are local application endpoints, not YNAB endpoints.

### 14.1 Suggested REST endpoints

#### Setup / settings
- `POST /api/settings/ynab-token`
- `GET /api/settings`
- `PATCH /api/settings`

#### YNAB sync
- `POST /api/ynab/test-connection`
- `POST /api/ynab/sync/full`
- `POST /api/ynab/sync/incremental`
- `GET /api/ynab/plans`
- `GET /api/ynab/accounts`
- `GET /api/ynab/categories`
- `GET /api/ynab/payees`

#### Import + classify
- `POST /api/imports`
- `GET /api/imports/{id}`
- `POST /api/imports/{id}/classify`
- `GET /api/imports/{id}/predictions`

#### Review
- `PATCH /api/predictions/{id}`
- `POST /api/predictions/bulk-approve`
- `POST /api/predictions/bulk-update`
- `POST /api/predictions/{id}/create-rule`

#### Rules
- `GET /api/rules`
- `POST /api/rules`
- `PATCH /api/rules/{id}`
- `DELETE /api/rules/{id}`
- `POST /api/rules/test`

#### Model
- `GET /api/models`
- `POST /api/models/train`
- `GET /api/models/metrics`

#### Export / write-back
- `POST /api/imports/{id}/write-to-ynab`
- `POST /api/imports/{id}/export-csv`
- `GET /api/sync-runs`
- `GET /api/prediction-runs`

---

## 15. Detailed classification logic

## 15.1 Amount normalization
Derive signed amount as:
- if `Outflow` is present: negative amount
- if `Inflow` is present: positive amount
- if both absent or both present: validation error unless explicitly handled

Store both:
- display currency amount
- YNAB-compatible milliunits when writing to YNAB

## 15.2 Normalization pipeline
Suggested pipeline:
1. Unicode normalize to NFKC
2. Uppercase for comparison view
3. Remove repeated spaces
4. Replace punctuation separators with spaces where appropriate
5. Remove parenthesized country suffixes if noisy
6. Remove embedded bank-specific prefixes
7. Remove explicit dates like `DU 30.01.2026`
8. Remove IBAN-like tokens `CH..`
9. Remove postal code + city tails when recognized
10. Generate merchant stem from remaining tokens

## 15.3 Feature engineering
Recommended features:
- `cleaned_memo`
- `merchant_stem`
- char 3-5 grams from memo
- word 1-3 grams from memo
- signed amount
- absolute amount bucket
- account ID
- direction flag
- source category text
- source label text

## 15.4 Ensemble strategy
Recommended scoring strategy:
- Rule engine returns score 1.0 when matched
- Exact history returns score 0.98
- Fuzzy history returns score based on similarity and consistency
- ML returns calibrated probability
- Final decision chooses highest trustworthy source based on precedence and threshold

## 15.5 Explanation examples
- `Matched rule: payee = SBB Mobile, category = Public Transport`
- `Found 17 similar historical transactions; 16 used payee = SBB Mobile and category = Public Transport`
- `ML category confidence 0.93 based on memo tokens: SUNRISE, YALLO`

Implementation note: explanations must be clean text in the final product; the examples above only illustrate the content structure.

---

## 16. Deduplication strategy

Duplicate prevention is critical.

### 16.1 Local fingerprint
Create a deterministic local fingerprint from:
- account
- date
- signed amount
- normalized memo or raw memo hash

### 16.2 YNAB duplicate guard
Before writing a batch:
- fetch recent or relevant historical transactions for the target account/date range,
- compare local fingerprint to remote candidates,
- mark likely duplicates.

### 16.3 Duplicate policy
Possible statuses:
- `new`
- `duplicate-local`
- `duplicate-remote-likely`
- `duplicate-confirmed`
- `written`

### 16.4 Conflict UI
Potential duplicates must be clearly surfaced before write-back.

---

## 17. YNAB integration requirements

## 17.1 Authentication
For personal local use, use YNAB personal access token.

## 17.2 Plan/account/category/payee lookup
The app must store local caches of these objects and refresh them during sync.

## 17.3 Current API assumptions for implementation
The implementation should target the currently documented YNAB v1 API where:
- `/plans/{plan_id}` is the primary documented resource path,
- legacy `/budgets/{budget_id}` paths remain backward-compatible,
- API rate limit is 200 requests per hour per access token,
- delta requests are supported on major collection endpoints and should be used for incremental sync.

## 17.4 SDK usage
Using the official or community JS/Python SDK is optional. Direct REST calls are acceptable if wrapped behind a clear adapter.

## 17.5 Retry policy
Handle:
- transient 5xx errors,
- 429 rate limiting,
- network timeouts,
- token/auth failures.

Suggested policy:
- exponential backoff with jitter,
- rate-limit aware batching,
- resumable sync runs.

## 17.6 Write-back batching
Because of the rate limit, the app should batch carefully and avoid unnecessary per-row lookups during write-back.

---

## 18. Security and privacy requirements

### 18.1 Local secret storage
Store YNAB token in one of:
- OS keychain / keyring preferred,
- encrypted local secret store as fallback.

Do not store it in browser localStorage.

### 18.2 Local data storage
All imported CSVs, cached YNAB data, and predictions are sensitive financial data.
- Store under a configurable app data directory.
- Allow the user to purge caches and exports.
- Document backup behavior clearly.

### 18.3 Optional telemetry
Disabled by default.
No remote telemetry in MVP.

### 18.4 Third-party AI prohibition for MVP
Do not send transaction text to remote LLMs in MVP.

---

## 19. Testing requirements

## 19.1 Unit tests
Must cover:
- CSV parsing
- amount normalization
- memo normalization
- rule matching
- fuzzy matching logic
- confidence calculation
- duplicate detection
- YNAB adapter serialization

## 19.2 Integration tests
Must cover:
- full import -> classify -> review -> write flow
- YNAB sync with mocked API
- rate-limit handling
- retraining cycle

## 19.3 UI tests
Must cover:
- onboarding
- file upload
- review edits
- bulk approve
- create rule from correction

## 19.4 Golden dataset tests
Create a fixed fixture dataset from anonymized historical transactions and expected predictions.

---

## 20. Observability and debugging

### 20.1 Structured logs
Use JSON logs with run IDs and correlation IDs.

### 20.2 Debug mode
Enable a debug screen to inspect:
- normalized tokens,
- matched rules,
- top similar historical records,
- model probability distribution,
- serialized YNAB payload preview.

### 20.3 Audit trail
Every run should be reproducible using stored inputs + model version + config snapshot.

---

## 21. Deployment and runtime modes

## 21.1 Supported runtime modes
### Option A — local native development
- backend on localhost
- frontend on localhost
- SQLite file in local app data dir

### Option B — Docker Compose
- frontend container
- backend container
- bind-mounted data directory

### Option C — packaged desktop-like local web app (future)
- optional Tauri/Electron wrapper later

## 21.2 Recommended MVP runtime
Use local web app with separate backend and frontend, optionally dockerized.

---

## 22. Repository structure recommendation

```text
ynab-automation/
  README.md
  docs/
    specification.md
  backend/
    app/
      api/
      core/
      db/
      models/
      schemas/
      services/
        ynab/
        importer/
        normalization/
        rules/
        matching/
        ml/
        review/
      workers/
      tests/
    pyproject.toml
    alembic.ini
  frontend/
    src/
      api/
      components/
      pages/
      features/
      hooks/
      lib/
      types/
      tests/
    package.json
  data/
    .gitkeep
  docker-compose.yml
```

---

## 23. Implementation phases

## Phase 1 — foundations
Deliver:
- monorepo scaffolding
- settings storage
- YNAB token setup
- plan/account/category/payee sync
- CSV import and preview
- SQLite schema and migrations

## Phase 2 — deterministic automation
Deliver:
- normalization engine
- rule engine
- exact history reuse
- basic review UI
- CSV fallback export

Expected benefit:
- large reduction in manual work for recurring merchants

## Phase 3 — ML automation
Deliver:
- training dataset builder
- payee/category models
- confidence scoring
- fuzzy similarity lookup
- model metrics page

## Phase 4 — safe YNAB write-back
Deliver:
- dry run
- duplicate detection
- create transactions via API
- audit logs
- sync/write error handling

## Phase 5 — polish
Deliver:
- bulk actions
- rule creation from corrections
- metrics dashboard
- packaging / Docker polish

---

## 24. Acceptance criteria

The product is acceptable for v1 when all of the following are true:

### Setup
- User can connect to YNAB with a personal access token.
- User can select a plan and import account metadata.

### Historical training
- User can bootstrap local historical dataset from YNAB.
- App can retrain models from historical + correction data.

### Import
- User can upload the monthly CSV and preview parsed rows.
- Imported rows are validated and persisted.

### Classification
- App predicts payee and category for each row.
- Each prediction contains confidence and explanation.
- Deterministic rules override model predictions.

### Review
- User can review low-confidence items efficiently.
- User can correct payee/category and save corrections.
- User can create rules from corrections.

### Write-back
- User can run dry-run validation.
- User can write approved transactions to YNAB.
- Duplicate candidates are surfaced before writing.

### Quality
- Backend and frontend test suites pass.
- App handles malformed rows gracefully.
- Sync runs and prediction runs are logged.

---

## 25. Risks and mitigation

### Risk 1: Wrong category prediction on rare merchants
Mitigation:
- conservative thresholds
- review queue
- easy corrections
- fast rule creation

### Risk 2: Duplicate transactions written to YNAB
Mitigation:
- local fingerprinting
- recent remote lookup
- dry-run mode
- explicit duplicate warnings

### Risk 3: Overengineering with LLMs
Mitigation:
- classical ML first
- rules + history reuse first
- no remote AI dependency in MVP

### Risk 4: YNAB API changes
Mitigation:
- wrap all YNAB calls in one adapter
- version local API client
- keep schema mapping centralized

### Risk 5: Personal finance privacy concerns
Mitigation:
- local-first design
- no remote telemetry
- secret storage in OS keychain

---

## 26. Open questions to keep configurable, not blocking

These should not block implementation. Default choices should be provided.

- Should one CSV import always map to exactly one YNAB account? Default: yes.
- Should the app auto-create unknown payees in YNAB? Default: yes when writing by name, but surface in review.
- Should the app auto-approve transfers? Default: no.
- Should scheduled transactions influence prediction confidence? Default: later.
- Should the app use source `Catégorie` as a strong feature? Default: yes, but only as an auxiliary feature.
- Should the app support multiple plans? Default: yes in schema, single active plan in UI for MVP.

---

## 27. Explicit build instructions for AI coding agents

Use the following implementation rules:

1. Build the backend first.
2. Keep business logic out of the frontend.
3. Write all YNAB interactions through a dedicated adapter service.
4. Make normalization logic deterministic and well-tested.
5. Implement rules before ML.
6. Implement exact historical reuse before fuzzy matching.
7. Implement fuzzy matching before ML.
8. Add ML only after deterministic pipeline is stable.
9. Every prediction must include explanation metadata.
10. Make all thresholds configurable from backend settings.
11. Add migrations from the beginning.
12. Provide seed/demo fixtures for local testing.
13. Use typed schemas for all API requests/responses.
14. Add unit tests for every service module.
15. Never send user transaction data to third-party AI services in MVP.

---

## 28. Suggested development backlog

### Backend backlog
- [ ] Project scaffold
- [ ] Settings and secret storage
- [ ] YNAB client adapter
- [ ] Full sync endpoint
- [ ] Incremental sync endpoint
- [ ] CSV import parser
- [ ] Normalization service
- [ ] Rule engine
- [ ] Historical lookup service
- [ ] Fuzzy matcher
- [ ] Model training pipeline
- [ ] Prediction orchestrator
- [ ] Write-to-YNAB service
- [ ] Audit logging
- [ ] Metrics service

### Frontend backlog
- [ ] Setup wizard
- [ ] Import page
- [ ] Review table
- [ ] Transaction detail drawer
- [ ] Rules page
- [ ] Model page
- [ ] Sync history page
- [ ] Settings page

### QA backlog
- [ ] Unit tests
- [ ] Integration tests
- [ ] UI tests
- [ ] Fixture-based regression tests

---

## 29. Example normalized transaction flow using the provided CSV style

Example input row:
- Date: `2026-01-30`
- Memo: `ACHAT/PRESTATION TWINT DU 30.01.2026 SBB MOBILE BERN (CH)`
- Outflow: `2.40`
- Catégorie: `Mobilité // Transports publics`

Expected internal processing:
1. Signed amount = `-2.40`
2. Cleaned memo = `SBB MOBILE`
3. Merchant stem = `SBB MOBILE`
4. Rule engine checks explicit aliases
5. Historical lookup finds prior `SBB MOBILE` rows
6. Payee predicted = `SBB Mobile`
7. Category predicted = matching YNAB public transport category
8. Confidence high -> auto-approve
9. Write payload prepared for selected YNAB account

Example second input row:
- Memo: `DÉBIT CH0330000006100005458 ETAT DE VAUD IMPÔTS 1002 LAUSANNE`

Expected internal result:
- Merchant stem = `ETAT DE VAUD IMPOTS`
- Payee = `Etat de Vaud`
- Category = `Taxes` or mapped YNAB equivalent
- Explanation = rule or historical recurring merchant

---

## 30. AI handoff prompt template

Use the text below as the initial prompt for Claude Code / Google Antigravity.

```text
Build the application described in the attached specification.

Requirements:
- Follow the spec exactly unless a change is clearly required for implementation correctness.
- Use a monorepo with a FastAPI backend and React + TypeScript frontend.
- Start with backend foundations, migrations, YNAB sync, CSV import, normalization, and rule engine.
- Keep all YNAB API logic behind a dedicated adapter.
- Implement deterministic rules and historical matching before ML.
- Add comprehensive tests.
- Use SQLite for MVP.
- Make the app local-first and privacy-preserving.
- Add a review UI that supports bulk approval and manual correction.
- Write clear README setup instructions.
- Do not use remote LLM APIs in MVP.

Execution order:
1. Scaffold repository.
2. Implement database models and migrations.
3. Implement settings and secret storage.
4. Implement YNAB sync.
5. Implement CSV import and validation.
6. Implement normalization and rule engine.
7. Implement historical matching.
8. Implement review API and UI.
9. Implement ML baseline.
10. Implement safe write-back to YNAB.
11. Implement tests and polish.

Deliverables:
- working source code
- migration files
- tests
- README
- sample fixture data
- Docker Compose setup
```

---

## 31. Final recommendation

Build this as a local-first application with deterministic automation first and ML second.

The order of value is:
1. payee normalization,
2. payee-to-category reuse,
3. historical nearest-match reuse,
4. confidence-based review,
5. direct YNAB API write-back,
6. ML for the remaining ambiguous cases.

This order will deliver the fastest practical reduction in manual work while keeping the system understandable, safe, and maintainable.

# Design

## Context

- **Classification:**
  - The pipeline runs rules → exact history → fuzzy history (capped at 0.94) → ML. Each target keeps its own confidence, source and explanation, and categories are identified by local `categories.id`.
  - Rows are persisted with `edited_*` user values, and `reclassify` keeps user edits.
- **Push path:**
  - Write-back builds one payload per row from persisted values and the stored category ID, always with `approved=false`.
  - Outcomes are correlated by `import_id`, and rows are claimed before sending.
  - Sync now marks split parents (`is_split`) but stores no subtransactions.
- **Bank evidence:** Swisscard rows keep their raw fields in `evidence_json`, including `Merchant`, `Card number`, `Foreign Currency` and `Amount in foreign currency`. PostFinance rows keep their long free-text descriptions.
- **Where it runs:** imports run locally (`./dev.sh`, SQLite). Proton Bridge exposes IMAP on loopback (default `127.0.0.1:1143`, STARTTLS, self-signed certificate, Bridge-generated password per account). It isn't installed yet, so everything is built and tested against synthetic mail.
- **Dependencies:** no Anthropic SDK or PDF library is installed yet. The Python backend uses FastAPI, SQLAlchemy and pydantic v2.

## Goals / Non-Goals

**Goals:**
- Receipts, AI and splits are three independent layers. Each works without the others, so the app stays fully usable with no Bridge, no key, or neither.
- Every external side effect is explicit and bounded: no mailbox writes, AI calls only on request or within the budget, and YNAB writes only through the existing push path.

**Non-Goals:**
- Automatic background fetching or AI runs.
- Auto-acceptance of AI suggestions.
- Learning from YNAB split history.
- Hosted Bridge access.
- Measuring AI accuracy with the evaluation command. That's a follow-up, because it costs money per run.

## Decisions

### D1. Configuration and feature switches
`src/settings.py` reads the following from the environment (`backend/.env`):

| Setting | Values / default |
|---|---|
| `BRIDGE_IMAP_HOST` | `127.0.0.1` |
| `BRIDGE_IMAP_PORT` | `1143` |
| `BRIDGE_CERT_PATH` | optional exported Bridge certificate |
| `BRIDGE_MAILBOXES` | comma-separated labels, e.g. `nicolas,partner` |
| `BRIDGE_<LABEL>_USER` / `BRIDGE_<LABEL>_PASSWORD` | per mailbox |
| `BRIDGE_FOLDERS` | `INBOX` |
| `ANTHROPIC_API_KEY` | none |
| `AI_MODEL` | `claude-sonnet-5-5` |
| `AI_EXTRACTION_MODEL` | defaults to `AI_MODEL` |
| `AI_MONTHLY_BUDGET_USD` | `10` |
| `AI_MAX_CONCURRENCY` | `4` |

`GET /features` reports `{receipts: on|off + reason, ai: on|off + reason}` with no secret values. TLS: the certificate is verified against `BRIDGE_CERT_PATH` when set. Otherwise an unverified STARTTLS is allowed only when the host is a loopback address, and the status explains this.

### D2. Read-only IMAP adapter (`src/mail/imap_source.py`)
- **Library:** standard `imaplib`. Each folder is opened with `EXAMINE` (read-only). Messages are fetched with `BODY.PEEK[]` and `UID`, so read flags never change.
- **Forbidden commands:** a wrapper rejects any command outside an allowlist (`LOGIN`, `STARTTLS`, `EXAMINE`, `UID SEARCH`, `UID FETCH`, `LOGOUT`, `CAPABILITY`, `NOOP`). A test asserts that `STORE`, `COPY`, `MOVE`, `EXPUNGE`, `SELECT` and `APPEND` are never sent.
- **Cursor:** `mailbox_cursors(mailbox, folder, uidvalidity, last_uid, last_fetch_at, last_error)`. Only UIDs greater than `last_uid` are fetched. A UIDVALIDITY change resets `last_uid` to 0, and deduplication prevents duplicates.
- **Deduplication:** a receipt is keyed by `content_hash`, the SHA-256 of the normalized body plus attachment hashes. Each sighting goes into `receipt_sources(receipt_id, mailbox, folder, uid, message_id)`, so a receipt seen in both mailboxes is one receipt with two sources.
- **Testability:** a `FakeImapServer` in tests implements the allowlisted subset over in-memory `.eml` fixtures. The adapter is constructed with an injectable connection factory.
- **Alternative:** a third-party IMAP client library. Rejected, because the standard library suffices and an allowlist is easier to audit.

### D3. Receipt extraction (`src/mail/extract.py`)
1. **Local pre-check.** The parts considered are text/plain, text/html (HTML converted to text with the standard library's `html.parser`) and PDF attachments (via `pypdf`). A message is a receipt candidate only if it has an amount pattern next to a currency or total keyword, or a PDF attachment, plus at least one receipt marker (order/commande/Bestellung, invoice/facture/Rechnung, receipt/quittance/Beleg, total/montant, refund/remboursement/Rückerstattung). Otherwise it's `not_receipt` and costs no AI call.
2. **Local fields.** The total, currency and date are parsed with the existing `money` helpers when unambiguous.
3. **AI extraction.** For candidates, the text (or, if a PDF has no text layer, the PDF as a base64 `document` block) goes to `AI_EXTRACTION_MODEL` with structured output: merchant, reference, kind (purchase/refund/shipping/payment), purchase date, total, currency and items (description, quantity, amount). The `extraction_method` is recorded as `text`, `pdf_text` or `ai_document`. A local total that disagrees with the AI total marks the receipt `needs_check`.
4. **Storage.** `receipts` keeps the extracted fields, an `items_json`, a bounded text excerpt (8 KB, local only) and the status. Full raw messages are not stored.

### D4. Matching (`src/receipts/matching.py`)
Each candidate (receipt, row) pair gets a score in [0, 1]:

| Signal | Weight | Rule |
|---|---|---|
| Amount | 0.45 | Exact match of the absolute bank amount, or of the Swisscard foreign amount with the receipt currency. 0.25 within 2% when the receipt currency differs and no foreign amount is present. |
| Date | 0.25 | The row date minus the receipt date is between 0 and 7 days; full weight for 0–3 days, linear decay to 7 days, 0 outside −1…10 days. |
| Merchant | 0.25 | Token overlap between the normalized receipt merchant and the bank memo or Swisscard `Merchant`, ≥ 0.5. |
| Card hint | 0.05 | The receipt mentions the row's last four card digits. |

- **Auto-link:** score ≥ 0.75 and a unique mutual best match (no other row ≥ 0.6 for the receipt, and no other receipt ≥ 0.6 for the row).
- **Shown as candidates:** score ≥ 0.45.
- **Kinds:** refunds only match inflows, and purchases only outflows. Shipping and payment notices attach as `supporting` to an existing match with the same reference.
- **Partial shipments:** the user can link one receipt to several rows. The split suggestion then uses only the receipt items whose amounts fit each row (subset sum over items, capped at 12 items), and otherwise falls back to a single category.
- **Storage:** `receipt_matches(receipt_id, import_row_id, score, signals_json, state proposed|linked|rejected, origin auto|user)`.
- **Timing:** matching runs after receipt fetch and after upload, for unpushed rows of the active batch, within ±30 days of each receipt.

### D5. Claude client wrapper (`src/ai/client.py`)
- **SDK:** the official `anthropic` Python SDK. It is lazy-imported, so the app runs without a key.
- **Requests:** each call uses structured outputs (pydantic model, `messages.parse`) on the beta messages endpoint, with `fallbacks: "default"` and the beta `server-side-fallback-2026-07-01`. If the installed SDK doesn't support the fallback parameter, it's left out and a refusal is recorded as such.
- **Thinking:** `thinking: {type: "adaptive"}` with `output_config.effort` set to `low` for extraction and `medium` for categorization. `max_tokens` is 4000.
- **Handling the response:** `stop_reason == "refusal"` is checked before reading content. Errors are caught most-specific first: rate limit and server errors are retried by the SDK (2 retries); authentication and bad requests mark AI as misconfigured.
- **Prompt caching:** the system prompt, the plan's category list and the rules form a stable prefix with `cache_control`, built per request batch so it stays byte-identical. Volatile row data comes after the cache breakpoint.
- **Usage:** every call writes to `ai_usage(created_at, purpose, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, status)`. Cost comes from a per-model price table in code (Sonnet 5.5: $2/$10 per MTok, cache reads $0.20). The month-to-date sum is checked before each call against `AI_MONTHLY_BUDGET_USD`.
- **Testing:** the wrapper takes an injectable `transport` (a fake returning parsed objects and usage), so tests never call the network.

### D6. Categorization request and validation (`src/ai/categorize.py`)
**Input, as one user message of data blocks:**
- The row: date, signed amount, currency, bank memo, Swisscard evidence fields, account name.
- The linked receipt: merchant, items, total, kind.
- Allowed categories: `[{id, group, name}]` of non-deleted plan categories.
- Up to 8 historical examples: the top exact and fuzzy matches by the history matcher, each with memo, payee and category name/ID.
- The rules, as plain sentences.

The system prompt says the input blocks are untrusted data and the task is only to propose values from the allowed lists. The precedence (user correction > purpose evidenced by a receipt > similar history > merchant fallback) comes from the household settings.

**Output schema:**

| Field | Type |
|---|---|
| `payee` | string |
| `category_id` | string (an ID from the allowed list in the prompt), or null. Not a schema enum: with a real plan's ~300 categories the API rejects the schema as too complex to compile; the server-side check below enforces the allowed set |
| `splits` | list of `{category_id, amount_milliunits, memo}` |
| `evidence` | list of `{kind: receipt\|history\|rule\|bank, ref}` |
| `explanation` | ≤ 300 chars |
| `uncertainty` | `low\|medium\|high` |

**Validation (server-side):**
- IDs must be in the allowed set.
- Splits need at least 2 lines, the exact milliunit sum and the same sign.
- The payee must not be empty.
- Failure sets `ai_status=invalid` with the reason.

**Stored on the row:** `ai_payee`, `ai_category_id`, `ai_splits_json`, `ai_explanation`, `ai_uncertainty`, `ai_evidence_json`, `ai_status` (`none|suggested|invalid|refused|error|skipped_budget`), `ai_model` and `ai_requested_at`.

**Applying the result:** the precedence rule decides where the suggestion applies (D7). AI-filled targets get source `ai` and a confidence of 0.80 for `low` uncertainty, 0.65 for `medium` and 0.5 for `high`. These are always below auto-acceptance and are not measured accuracy. The row stays `pending`.

**Endpoints:**
- `POST /import-batches/{id}/ai-suggest` runs on rows whose payee or category isn't from rules, user edits or confident history (≥ 0.95), up to the budget, with `AI_MAX_CONCURRENCY` threads.
- `POST /import-rows/{id}/ai-suggest` runs on one row.
- `GET /ai/usage` returns month-to-date usage and the budget.

### D7. Precedence merge
A shared function `apply_precedence(row, prediction, ai, user_edits)` ranks the sources per target:

1. user edit
2. rule
3. history ≥ 0.95
4. AI with receipt evidence
5. other history
6. AI without receipt
7. ML
8. unclassified

Replaced suggestions are kept in an `alternatives_json` (source, value, confidence) shown in the tooltip. Reclassify and AI-suggest both call it, so their order of execution doesn't matter.

### D8. Splits (`src/splits.py`)
- **Storage:** `import_row_splits(id, row_id, position, amount_milliunits, category_id, payee, memo, origin receipt|ai|user)`.
- **Endpoints:** `PUT /import-rows/{id}/splits` replaces the lines, after validation, with the same rules as D6 plus not deleted and not a transfer. `DELETE /import-rows/{id}/splits` clears them.
- **Push:** if a row has lines, write-back validates them again (a deleted category or a wrong sum blocks the row with a specific error). The payload gets `category_id: null` and `subtransactions: [{amount, category_id: <ynab id>, memo, payee_id|payee_name}]`, with the import ID, the amount and `approved: false` unchanged. Dry runs validate only.
- **Sent values:** `sent_category_id` stays null for split rows, and `sent_splits_json` records what was sent.
- **Sync:** split parents already have provenance `split_parent`, which excludes them from training.
- **Frontend:** a split editor in an expandable row of the review table. It has category pickers by ID, amounts in currency units converted to milliunits exactly, a live "unassigned" amount and "Use receipt split" or "Use AI split" buttons.

### D9. Frontend
- **Review table:**
  - A receipt badge, with a popover listing merchant, items and match signals, and Link/Reject buttons for candidates.
  - An "AI" source badge with uncertainty, explanation and alternatives.
  - The split editor.
  - "Ask AI (n rows, ≈ $x.xx)" in the review header. The estimate comes from recent average cost per request.
- **Settings:**
  - Mailboxes: status per mailbox, last fetch, "Fetch receipts" and errors.
  - AI: on/off reason, model, month-to-date usage and budget.

## Risks / Trade-offs

- [Bridge's self-signed certificate] → certificate pinning via `BRIDGE_CERT_PATH`, with unverified TLS allowed only on loopback and stated in the status.
- [Prompt injection through email content] → data-only framing, schema-restricted output, server-side validation and no tool use. The AI has no capability beyond returning a suggestion (tested with an injection fixture).
- [Cost overrun] → a hard monthly cap checked before every call, explicit user-triggered runs, a local pre-check before extraction and prompt caching of the stable prefix.
- [Matching mistakes link the wrong receipt] → only unique strong mutual matches auto-link. Everything else needs a click, links can be undone, and links never change bank values.
- [Split arithmetic errors] → integer milliunits only, exact-sum validation in three places (AI output, save and push) and a live unassigned-amount display.
- [Model behaviour can't be unit-tested] → tests cover request construction, validation, budget, refusal, precedence and persistence with a fake transport. Real quality is checked manually on a small sample after you add a key, and measured later with the evaluation command.
- [`pypdf` can't read scanned PDFs] → falls back to sending the PDF as a document block (billed, counted in usage).
- [SDK fallback parameter availability varies by SDK version] → the wrapper detects support at call time and records whether fallbacks were active.

## Migration Plan

1. One additive Alembic revision adds:
   - New tables: `mailbox_cursors`, `receipts`, `receipt_sources`, `receipt_matches`, `import_row_splits` and `ai_usage`.
   - New columns on `import_rows`: `ai_payee`, `ai_category_id`, `ai_splits_json`, `ai_explanation`, `ai_uncertainty`, `ai_evidence_json`, `ai_status`, `ai_model`, `ai_requested_at`, `alternatives_json` and `sent_splits_json`.

   There is no backfill.
2. Add `anthropic` and `pypdf` (pinned) to `requirements.txt`.
3. Add the new variables to `backend/.env.example` with comments. The hosted deploy keeps the features off unless configured.
4. Rollback: downgrade drops the tables and columns. Existing rows without splits or AI data are unaffected.

## Open Questions

- The household rules for work lunch and holiday purposes are still unset (`household-settings.example.json`). The AI receives only the existing rules and history until they are defined.
- When Bridge is installed, which folders hold receipts (for example a "Receipts" label) is a configuration value (`BRIDGE_FOLDERS`), not a code change.

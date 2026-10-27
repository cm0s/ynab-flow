# Design

## Context

See proposal.md for motivation.

Current state:
- **`Receipt` rows** (`backend/src/models.py`) hold one set of values per field (merchant, date, total, currency, kind, reference, `items_json`). The fetch (`src/mail/fetch.py`) and AI extraction (`src/ai/extract.py`) write those columns directly.
- **Skipped messages** are `Receipt` rows with status `not_receipt`. Only their subject, sender and message date are stored, with no excerpt.
- **Each sighting** of a message is a `ReceiptSource` (mailbox, folder, UIDVALIDITY, UID, Message-ID).
- **Matching** (`src/receipts/matching.py`) scores any receipt against any row (`score`). However, `run_matching` and `match_active_batches` only consider unpushed rows of active imports. `decide` records link, unlink and reject decisions with `origin="user"`.
- **Rows keep their category** after push: `edited_category_id` or `category_id`, plus `sent_category_id` once pushed. Split rows have `ImportRowSplit` lines. For lines filled with "Use receipt split", the memo is the item description.
- **IMAP access** (`src/mail/imap_source.py`) only allows CAPABILITY, STARTTLS, LOGIN, EXAMINE, UID SEARCH, UID FETCH (`BODY.PEEK[]`), LOGOUT and NOOP.
- **AI calls** go through `AIClient.call` (budget reservation, `AIUsage` rows by purpose). Categories are sent as plain-string IDs in the output schema and validated after the answer, because an enum of the plan's ~300 IDs is rejected by the API.
- **Receipts are not tied to a plan**; categories are. The household uses one plan.
- **The frontend** has no chart library. Views live in `App.tsx` (`import | review | push | settings | dashboard`).

## Goals / Non-Goals

**Goals:**
- Corrections are lossless: the extracted value is always recoverable, and re-reads never undo user work.
- Spending figures can be traced to receipts and items and always add up exactly. Integer milliunits are used throughout, with no floating-point totals.
- New AI use is opt-in, estimated beforehand, and budgeted like the existing calls.

**Non-Goals:**
- Linking receipts to YNAB transactions that never went through an import. Analysis covers receipts and import rows only.
- Currency conversion from exchange rates. Only linked bank amounts are used to convert.
- Charts beyond simple bars and tables; budgets or forecasts; editing YNAB from this page.
- Multi-plan receipt ownership.

## Decisions

### D1. Corrections: user values in place, extracted values in a snapshot
Add three columns to `receipts`:
- `extracted_json`: the last extracted values of the editable fields.
- `user_fields_json`: the list of fields the user corrected.
- `excluded` (boolean): excluded from spending.

How the columns are used:
- The ordinary columns keep holding the **effective** value, so matching, splits, Ask AI and the existing endpoints keep working unchanged.
- A correction writes the column and adds the field to `user_fields`.
- A reset copies the field back from `extracted_json` and removes it from `user_fields`.
- Every extraction (fetch, rescue, AI re-read) goes through one `apply_extraction(receipt, values)` helper. It always refreshes `extracted_json` and writes only columns whose field is not in `user_fields`.
- The migration backfills `extracted_json` from the current columns.

*Alternative:* a separate overrides table merged at read time. Rejected: every reader of receipts (matching, splits, AI context, views) would need the merge.

### D2. Classification and the "items" field
- *Not a receipt* and *this is a receipt* set `status` and add `classification` to `user_fields`. The fetch's re-extraction of candidates then never changes the status back.
- Items are one correctable field (`items`). Setting an item's category through the user also marks `items` as user-owned, so a re-read cannot renumber or drop a categorized item.

*Alternative:* track user values per item. Rejected: re-reads can reorder or merge items, so per-item identity is unreliable.

### D3. Rescue re-reads the message read-only, by UID then Message-ID
For a skipped message marked as a receipt, take its newest `ReceiptSource`, connect through the existing guarded connection and `EXAMINE` the folder:
- If UIDVALIDITY is unchanged, `UID FETCH <uid> (BODY.PEEK[])`.
- Otherwise run `UID SEARCH HEADER Message-ID "<id>"`, which `UID SEARCH` already allows, then fetch.

The message is then parsed and analysed like a fetched one and extracted with AI when it is on. The local candidate check is bypassed, because the user said it is a receipt. If Bridge is unreachable, the status becomes `candidate` with the error stored, and the next fetch or a retry completes it.

No new IMAP command is added. A single-message `fetch_one(settings, source)` helper lives in `src/mail/fetch.py`.

### D4. AI re-read reuses extraction, AI item categorization is a new purpose
- **Re-read** runs `extract_receipt` on the message re-fetched as in D3, so PDFs are included. Only when Bridge cannot provide it does the re-read fall back to the stored excerpt. It goes through `apply_extraction`.
- **Item categorization** sends one request per receipt, using the existing cached system prefix (categories by ID, household rules) and a user block containing:
  - the merchant, date and items, marked as untrusted data;
  - the linked row's bank text if any.

  The output is a list of `{index, category_id}`, with category IDs as plain strings, validated against live categories. Invalid entries stay uncategorized and the reason is recorded. Model: `AI_MODEL`, effort `low`. Usage is recorded with purpose `categorize_items`.
- **Estimates** are the request count × `average_request_cost(purpose)`, using the existing default until real samples exist. They are returned by a dry-run call of the same endpoint (`estimate_only: true`), so the number shown is the one that will be spent.

### D5. Item categories: stored only for user and AI; split and row resolved at read time
- **Stored values:** each item in `items_json` may carry `category_id` and `category_source` (`user` or `ai`).
- **Resolved when a report is built**, in the order given by the spec:
  1. user;
  2. split line of a linked primary row whose memo equals the item description (case- and whitespace-insensitive), or, when the row has exactly one split line per item in the same order and amounts, that line;
  3. the linked row's single category: `sent_category_id`, else `edited_category_id`, else `category_id`;
  4. AI;
  5. Uncategorized.
- **Deleted categories** are resolved through `CategoryDirectory.is_deleted`.

Resolving at read time means later row edits and splits are reflected without recomputation.

### D6. Counting and scaling with exact integers
Counted receipts are those with kind `purchase` or `refund` and status `extracted` or `needs_check` (or user-classified as a receipt), not `excluded`. Refunds count negatively. The reporting date is the purchase date, else the message date.

How a receipt is counted:
- **Target amount:**
  - linked: the sum of the absolute amounts of its linked primary rows, in the plan currency;
  - unlinked in the plan currency: its total;
  - unlinked in another currency: listed separately, uncounted.
- **Item lines:** when the items do not add up to the receipt total, an "Other (fees, discounts)" line holds the difference (it may be negative for a discount). Items read without prices (all amounts 0) share the target equally. A receipt without items becomes one line named after the merchant.
- **Scaling:** lines are scaled to the target with the largest-remainder method, so they sum exactly.

### D7. Spending computed on request in Python
`GET /spending` loads the counted receipts of the date range (hundreds to low thousands) with their linked rows and splits, resolves the lines, and groups them by category, merchant or normalised item description, with per-month totals. Each group carries the receipt and item references behind it.

No materialised table is kept: correctness after edits matters more than speed at this volume. If it becomes slow, an aggregate cache keyed by receipt `updated_at` can be added without changing the API.

### D8. API
| Method | Path | Purpose |
|---|---|---|
| `GET` | `/receipts` | Filtered, paginated list (`status`, `kind`, `linked`, `mailbox`, `q`, `from`, `to`, `page`, `page_size`) |
| `GET` | `/receipts/{id}` | Detail: fields, `user_fields`, `extracted` values, items with resolved category, sources, excerpt, links and proposals |
| `PATCH` | `/receipts/{id}` | Corrections (`fields`), `reset: [field]`, `excluded`; validates and re-runs matching (D9) |
| `POST` | `/receipts/{id}/classify` | `{as: "receipt" \| "not_receipt"}`; a rescue triggers D3 |
| `POST` | `/receipts/{id}/reread` | AI re-read; `estimate_only` supported |
| `GET` | `/receipts/{id}/candidates` | Rows from any import within ±30 days, scored |
| `POST` | `/receipts/{id}/links` | `{row_id, action}`; wraps the existing `decide` |
| `PUT` | `/receipts/{id}/items/{index}/category` | User item category (`null` clears it) |
| `POST` | `/receipts/categorize-items` | `{receipt_ids}` or the current filter; `estimate_only` supported |
| `GET` | `/spending` | `plan_id`, `from`, `to`, `group` (`category` \| `merchant` \| `item`), `q`; totals, months, uncategorized share, uncounted list |

All responses exclude credentials. Mailbox labels (`me`, `partner`) are shown, never usernames.

### D9. Matching after corrections; manual links to pushed rows
After a correction to total, date, merchant, currency or kind, or after a rescue, `run_matching` is called for the active rows within ±30 days of the receipt. Existing `origin="user"` decisions are kept, as today.

The candidate search scores rows of every batch, with no write-state filter. Linking uses `decide` unchanged: it never touches row amounts, write state or YNAB. `run_matching` keeps its unpushed-only filter, so automatic links never reach pushed rows.

### D10. Frontend
- A new `receipts` view (header icon next to Dashboard) with two tabs.
- **Receipts tab:**
  - filter bar and paginated table;
  - a side panel for the detail, with an edit form, reset buttons per corrected field, classification and exclusion toggles, *Read again with AI* (shows the estimate, then confirms), and candidate rows with Link/Unlink;
  - item rows with a category picker (the existing `SearchablePicker`) and a source tag.
- **Spending tab:**
  - date range, group selector and search;
  - a table with per-month columns and CSS bars;
  - a drill-down list of items;
  - *Categorize items* for the uncategorized items in view, with estimate and confirm.
- The 🧾 badge popover gets a link to the receipt's detail.

No new frontend dependency.

## Risks / Trade-offs

- [Item-to-split mapping is heuristic] → only exact description or one-to-one positional matches count as *split*; anything else falls to row, AI or uncategorized, and the source is always shown.
- [Scaling to the bank amount attributes fees and FX margins to items proportionally] → documented; the "Other" line keeps explicit fees visible before scaling.
- [The same purchase stored as two receipts (confirmation and invoice) double-counts] → *exclude from spending*; the receipts list shows possible duplicates (same merchant, total and date ±3 days) with a marker, but nothing is excluded automatically.
- [Rescue depends on Bridge still holding the message] → the source's Message-ID fallback handles UIDVALIDITY resets. A deleted message leaves the receipt as `candidate` with the error and allows manual entry of fields.
- [AI categorization cost on many receipts] → per-receipt requests with a shared cached prefix, the estimate shown before confirming, and the hard monthly budget.
- [Spending computed on each request] → acceptable at household volume; D7 notes the cache path.

## Migration Plan

1. Alembic migration adds `extracted_json` (default `{}`), `user_fields_json` (default `[]`) and `excluded` (default false) to `receipts`, and backfills `extracted_json` from current columns. Downgrade drops the three columns; effective values stay in place.
2. Existing `items_json` entries need no change, since the new item keys are optional.
3. Deploy the backend and frontend together, as in the earlier phases. Render runs the migration on start; locally, `alembic upgrade head`.

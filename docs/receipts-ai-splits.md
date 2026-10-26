# Receipts, AI suggestions and split transactions

Three optional layers on top of the import pipeline. Each works without the
others, and the app stays fully usable with none of them configured.

## Split transactions

A split records one bank transaction under several categories, for example a
supermarket receipt with food and household items.

**Rules** (checked when saving, again at push time, and on AI output):

- At least two lines.
- Line amounts are integer milliunits and sum **exactly** to the bank amount.
- Every line has the same sign as the bank amount (no zero lines).
- Every category exists in the plan and is not deleted in YNAB.
- Transfers and card repayments cannot be split.

The bank amount, date, account and import identity never change.

**In the review table**, the split button (⑂) on a row opens the editor:

- Type amounts without a sign; the sign comes from the bank amount.
- The badge shows the amount still unassigned (or over-assigned), live.
- "Save split" stores the lines; "Clear split" returns the row to a single
  category. Suggested splits ("Use receipt split", "Use AI split") fill the
  editor and are saved only when you click Save.

**Pushing**: a split row is sent as one YNAB transaction with
`category_id: null` and `subtransactions` (amount, category, optional memo and
payee), keeping its import ID and `approved: false`. If a line's category was
deleted in YNAB after saving, the row is blocked with a message naming the
category. A dry run validates splits and sends nothing. What was sent is kept
in `sent_splits_json`.

**API**

| Method | Path | Body / result |
|---|---|---|
| `PUT` | `/import-rows/{row_id}/splits` | `{"lines": [{"amount_milliunits": -62400, "category_id": "<local id>", "memo": "", "payee": "", "origin": "user"}]}` → the row; `422` with the reason when invalid |
| `DELETE` | `/import-rows/{row_id}/splits` | → the row without lines |

Rows in batch payloads carry their lines in `splits`.

## Receipts from Proton Mail Bridge

Receipts are read from the household's Proton mailboxes through
[Proton Mail Bridge](https://proton.me/mail/bridge) running on the same
computer as the backend. Access is strictly read-only:

- Folders are opened with `EXAMINE` and messages fetched with `BODY.PEEK[]`,
  so nothing is moved, deleted, flagged or marked as read.
- The IMAP connection refuses, before sending, any command outside
  `CAPABILITY, STARTTLS, LOGIN, EXAMINE, UID SEARCH, UID FETCH, LOGOUT, NOOP`.
- Passwords stay in `backend/.env`; they are never logged, returned by the
  API or sent to the AI.

### Setup (both accounts)

1. Install Proton Mail Bridge and sign in with **each** household account.
2. In Bridge, open each account's *IMAP/SMTP settings* and note the
   username and the **Bridge-generated password** (not the Proton password).
   The IMAP server is normally `127.0.0.1`, port `1143`, security STARTTLS.
3. Optional but recommended: export Bridge's TLS certificate (in Bridge's
   advanced settings; the exact menu name depends on the Bridge version) and
   set `BRIDGE_CERT_PATH` to the exported `cert.pem` file. Without it, the certificate is not verified, which is
   allowed only when Bridge runs on `127.0.0.1`/`localhost`.
4. Add to `backend/.env` (labels are free-form; they name the variables):

   ```dotenv
   BRIDGE_IMAP_HOST=127.0.0.1
   BRIDGE_IMAP_PORT=1143
   BRIDGE_CERT_PATH=/path/to/cert.pem
   BRIDGE_MAILBOXES=me,partner
   BRIDGE_ME_USER=me@proton.me
   BRIDGE_ME_PASSWORD=<bridge password>
   BRIDGE_PARTNER_USER=partner@proton.me
   BRIDGE_PARTNER_PASSWORD=<bridge password>
   BRIDGE_FOLDERS=INBOX
   ```

   `BRIDGE_FOLDERS` is a comma-separated list of folders or labels to read,
   for example `INBOX,Receipts` (Proton labels appear as `Labels/<name>`).
5. Restart the backend and use **Settings → Receipts from Proton Mail →
   Fetch receipts**. The section shows each mailbox's last fetch and any
   error in plain terms (Bridge not running, login rejected, unknown folder).

### What a fetch does

- Each mailbox folder keeps a cursor; only messages newer than the last one
  read are fetched. If Bridge resets a folder (UIDVALIDITY change), it is
  read again without creating duplicates.
- A receipt is identified by its content, so the same receipt in both
  mailboxes is stored once with both sources. The mailbox does not imply who
  the purchase was for or which account paid.
- A local check reads plain text, HTML and PDF text layers. Messages without
  receipt markers and amounts (newsletters) are skipped and cost nothing.
- With AI on, receipt candidates are read by `AI_EXTRACTION_MODEL` (the PDF
  itself is sent only when it has no text layer). A receipt whose AI total
  disagrees with the locally read total is marked *needs a check*. Without
  AI, receipts keep the locally read total, date, currency and reference.
- Only extracted fields and a bounded text excerpt (8 KB) are stored, in the
  local database. Full messages are not stored.

### API

| Method | Path | Result |
|---|---|---|
| `GET` | `/features` | `{receipts: {enabled, reason}, ai: {enabled, reason, model}}`; never secret values |
| `GET` | `/receipts/status` | per mailbox and folder: last fetch and error; receipt counts by status |
| `POST` | `/receipts/fetch` | counts of messages read, new receipts, duplicates and non-receipts; `409` when not configured or already running |

## Receipt matching

After every receipt fetch and every upload, receipts are matched to the
unpushed rows of the active import (transfers and repayments excluded, within
±30 days). Each pair is scored:

| Signal | Weight | Rule |
|---|---|---|
| Amount | 0.45 | Exact bank amount, or the Swisscard foreign amount in the receipt currency; 0.25 within 2% for another currency without a foreign amount; 0.30 when a subset of the receipt's items sums exactly to the row (partial shipment) |
| Date | 0.25 | Full weight when the row posts 1 day before to 3 days after the purchase, decreasing to 0 at 10 days |
| Merchant | 0.25 | At least half of the receipt merchant's words appear in the bank text or Swisscard merchant |
| Card | 0.05 | The receipt mentions the row's last four card digits |

- **Linked automatically** only at ≥ 0.75 and when the pair is the unique
  mutual best (no other row ≥ 0.6 for the receipt, no other receipt ≥ 0.6 for
  the row). Pairs ≥ 0.45 are shown as candidates.
- Refunds match only inflows, purchases only outflows. Shipping and payment
  notices with the same reference attach as supporting evidence to a linked
  purchase.
- The receipt badge (🧾) on a row opens the details: items, match signals and
  Link / Unlink / Not this purchase. One receipt can be linked to several rows
  (partial shipments); your decisions are kept when matching runs again.
- **Receipts never change bank values**: amount, date, account, transfer
  routing, import ID and reconciliation state stay as imported. When a
  receipt total differs from the bank amount (discount, fee), "Use receipt
  split" puts the difference into the largest item so the lines still add up.

`POST /import-rows/{row_id}/receipts` with `{"receipt_id": "…", "action": "link" | "unlink" | "reject"}`
returns the row; rows carry their receipt evidence in `receipts`.

## AI suggestions (Claude)

AI is **off** until `ANTHROPIC_API_KEY` is set in `backend/.env`:

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Turns AI on |
| `AI_MODEL` | `claude-sonnet-5-5` | Model for categorization |
| `AI_EXTRACTION_MODEL` | `AI_MODEL` | Model for reading receipts |
| `AI_MONTHLY_BUDGET_USD` | `10` | Hard monthly cap |
| `AI_MAX_CONCURRENCY` | `4` | Parallel requests during "Ask AI" |

### What it does

"Ask AI (n rows, ≈ $x)" in the review header asks for a payee, a category and
optionally split lines for each row that is **not** already settled by your
edits, a rule or confident history (≥ 0.95). Each request contains only:

- the row's bank evidence (date, amount, currency, bank text, account and
  Swisscard merchant fields; no card number),
- the linked receipt's merchant, kind, date, total and items (not the email),
- the plan's live categories by ID, and the household rules as sentences,
- up to 8 similar past transactions (trusted history only).

Bank and receipt text is marked as untrusted data, never instructions. The
answer is structured output and is validated by the server: unknown category
IDs, splits that do not add up exactly or lack a payee are rejected and
recorded as *AI invalid*; the row is left as it was. The AI cannot write to
YNAB, change bank values, route transfers or link receipts.

### Precedence

Each target (payee, category) takes the highest-ranked suggestion:

1. your edit
2. rule
3. history ≥ 0.95
4. AI backed by a linked receipt
5. other history
6. AI without receipt evidence
7. ML
8. unclassified

Replaced suggestions stay visible as alternatives in the source and
confidence tooltips. AI-filled targets show the ✨ AI source with confidence
0.80 / 0.65 / 0.50 for low / medium / high uncertainty. This is not measured
accuracy, it is always below auto-acceptance, and AI-suggested rows stay
**pending** until you accept them. A suggested split is offered as "Use AI
split" in the split editor and saved only when you click Save. Reclassify and
Ask AI apply the same merge, so their order does not matter.

### Cost controls

- Every request (categorization and receipt reading) records its tokens and
  estimated cost; Settings → AI suggestions shows this month's spend.
- Before each request the month-to-date cost plus an estimate is checked
  against `AI_MONTHLY_BUDGET_USD`; once reached, requests stop with a message
  (rows show *AI budget reached*; receipts wait for a later fetch).
- AI runs only when you click "Ask AI" or "Fetch receipts"; newsletters and
  other non-receipts are filtered locally first.
- The stable part of each request (instructions, categories, rules) is
  marked for prompt caching.
- Prices used for estimates (USD per million tokens): Sonnet 5.5 $2 input /
  $10 output / $0.20 cache read.

### Refusals

Requests use Anthropic's server-side refusal fallback (`fallbacks: "default"`,
beta `server-side-fallback-2026-07-01`) where the model supports it: if Claude
declines on safety grounds, Anthropic retries on another model. A request that
is still declined is recorded as *AI declined* and changes nothing.

### Privacy

Receipts and mailbox data stay in the local database. Only the fields listed
above are sent to Anthropic, and only when you ask. Mailbox passwords and the
API key are never sent, logged or returned by the API.

### API

| Method | Path | Result |
|---|---|---|
| `POST` | `/import-batches/{batch_id}/ai-suggest` | Optional `{"row_ids": [...]}`; counts per outcome, month usage, and the batch rows |
| `POST` | `/import-rows/{row_id}/ai-suggest` | One row; `409` when it is already settled |
| `GET` | `/ai/usage` | Month-to-date spend, budget, requests per purpose, estimate per request |

Rows carry the stored AI result in `ai` and replaced suggestions in
`alternatives`.

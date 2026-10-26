# receipt-ingestion Specification

## Purpose

Retrieve purchase receipts from the household's local Proton Mail Bridge mailboxes, read-only and incrementally, and turn them into structured purchase evidence without exposing credentials.

## Requirements

### Requirement: Read mailboxes read-only through the local Bridge
The system SHALL connect to one or more configured mailboxes through a local Proton Mail Bridge IMAP endpoint and SHALL never delete, move, flag or mark messages as read. Credentials SHALL come only from local configuration and SHALL never be logged, returned by the API or sent to an AI provider.

#### Scenario: Fetch without side effects
- **WHEN** receipts are fetched from a mailbox
- **THEN** every message keeps its read state and location, and no IMAP write command is issued

#### Scenario: Bridge not configured or not running
- **WHEN** no mailbox is configured, or the Bridge endpoint cannot be reached
- **THEN** the mailbox status reports the problem in plain terms and the rest of the app keeps working

### Requirement: Process mailboxes incrementally with stable identities
The system SHALL keep a cursor per mailbox and folder, process only messages it has not seen, and identify each message by its mailbox, message ID and content hash so a repeated fetch creates no duplicates. A folder whose UID validity changes SHALL be rescanned without duplicating known messages.

#### Scenario: Repeated fetch
- **WHEN** a fetch runs twice with no new mail
- **THEN** the second run stores no new receipts

#### Scenario: Same receipt in two mailboxes
- **WHEN** the same receipt arrives in both household mailboxes
- **THEN** it is recognised as one receipt and both mailbox sources are kept

### Requirement: Extract structured purchase details
The system SHALL extract the merchant, purchased items, an order or invoice reference, the total and currency, the payment or purchase date and the receipt type (purchase, refund, shipping or payment notice). It SHALL read plain-text and HTML bodies and PDF attachments first, and use AI document reading only when no usable text exists. Messages that are not receipts SHALL be skipped without AI extraction where a local check can tell.

#### Scenario: PDF invoice
- **WHEN** an email carries a PDF invoice with a text layer
- **THEN** the total, currency, date, merchant and line items are extracted from the PDF text

#### Scenario: Scanned receipt
- **WHEN** a PDF attachment has no text layer
- **THEN** the document is read with AI document input, and the result is marked as extracted that way

#### Scenario: Newsletter
- **WHEN** a message has no amount and no receipt markers
- **THEN** it is skipped and counted as not a receipt, without an AI call

### Requirement: Keep mailbox identity separate from purchase beneficiary
The system SHALL record which mailbox a receipt came from and SHALL NOT infer from it who the purchase was for or which account paid.

#### Scenario: Receipt in a partner's mailbox
- **WHEN** a receipt arrives in the second household mailbox
- **THEN** it is available for matching to any account's rows, and no owner or purpose is assumed from the mailbox

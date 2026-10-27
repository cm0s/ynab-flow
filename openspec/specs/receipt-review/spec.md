# receipt-review Specification

## Purpose

Let the household find, inspect and correct every stored receipt and skipped message, so that receipt evidence used for matching, splits, AI suggestions and spending analysis can be checked and trusted.

## Requirements

### Requirement: List and filter receipts
The system SHALL list all stored receipts and skipped messages with their merchant, date, total, currency, kind, status, mailbox sources and number of linked rows. The list SHALL be filterable by status, kind, linked or unlinked, mailbox, merchant text and date range, and SHALL be paginated.

#### Scenario: Find receipts that need a check
- **WHEN** the user filters by status *needs a check*
- **THEN** only receipts whose AI total disagrees with the locally read total are listed

#### Scenario: Find unlinked purchases
- **WHEN** the user filters by kind *purchase* and *unlinked*
- **THEN** only purchase receipts without a linked row are listed

### Requirement: Show a receipt with its evidence
The detail of a receipt SHALL show its extracted fields and items, the subject, sender and mailbox sources of its messages, the stored text excerpt, how it was extracted, any extraction error, and the rows it is linked or proposed to. A receipt marked *needs a check* SHALL show the AI total and the locally read total side by side.

#### Scenario: Doubtful total
- **WHEN** the user opens a receipt whose AI total is CHF 119.00 and whose PDF total is CHF 120.00
- **THEN** both totals are shown with their source

#### Scenario: Mailbox credentials never shown
- **WHEN** any receipt detail is returned
- **THEN** it contains no mailbox password or API key

### Requirement: Correct receipt fields and items
The user SHALL be able to edit a receipt's merchant, purchase date, total, currency, kind, reference and items (description, quantity, amount). Amounts SHALL be validated as positive values in the receipt currency. Each corrected field SHALL be recorded as a user value, and the receipt SHALL show which fields were corrected.

#### Scenario: Fix a missing total
- **WHEN** the user sets the total of a receipt read as 0 to CHF 51.00
- **THEN** the receipt stores CHF 51.00 as a user value and shows that the total was corrected

#### Scenario: Invalid amount
- **WHEN** the user enters a negative or non-numeric item amount
- **THEN** the correction is rejected with the reason and nothing is changed

### Requirement: Keep user corrections authoritative
A later fetch, a repeated sighting of the same message, or an AI re-read SHALL NOT overwrite a field the user corrected. Fields the user did not correct MAY be updated by a re-read. The user SHALL be able to reset a field to the extracted value.

#### Scenario: Re-read after a correction
- **WHEN** the user has corrected the merchant and then asks for an AI re-read that returns another merchant and new items
- **THEN** the user's merchant is kept and the uncorrected items are replaced by the re-read items

#### Scenario: Reset a correction
- **WHEN** the user resets a corrected total
- **THEN** the extracted total is shown again and the field is no longer marked as corrected

### Requirement: Reclassify messages as receipt or not
The user SHALL be able to mark a receipt as *not a receipt* and a skipped message as *a receipt*. A message marked as a receipt SHALL be read again from its mailbox through the read-only Bridge access, because only its subject and sender were stored. The user's classification SHALL survive later fetches.

#### Scenario: Rescue a wrongly skipped invoice
- **WHEN** the user marks a skipped message as a receipt while Bridge is running
- **THEN** the message is fetched again without changing its read state or location, its details are extracted, and it becomes a receipt available for matching

#### Scenario: Bridge not running
- **WHEN** the user marks a skipped message as a receipt and Bridge cannot be reached
- **THEN** the classification is kept, the receipt waits for extraction, and the reason is shown

### Requirement: Read one receipt again with AI on request
The user SHALL be able to ask for one receipt to be read again by AI. The estimated cost SHALL be shown before the request, the request SHALL count against the monthly AI budget, and a refused, invalid or over-budget request SHALL leave the receipt unchanged and show the reason.

#### Scenario: Budget reached
- **WHEN** the monthly AI budget is reached and the user asks for a re-read
- **THEN** no request is sent and the receipt is unchanged

### Requirement: Exclude a receipt from spending
The user SHALL be able to mark a receipt as excluded from spending, for example a duplicate confirmation of a purchase already counted, and to undo it. Exclusion SHALL NOT remove the receipt or its links.

#### Scenario: Order confirmation and invoice for one purchase
- **WHEN** the user excludes the confirmation email of a purchase whose invoice is also stored
- **THEN** the purchase is counted once in spending and both receipts remain visible

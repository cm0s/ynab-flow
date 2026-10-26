# receipt-matching Specification

## Purpose

Link extracted receipts to bank import rows conservatively, so receipts enrich the bank record without ever changing it.

## Requirements

### Requirement: Match receipts using several signals
The system SHALL propose receipt matches for import rows from amount agreement (exact, or within currency conversion tolerance for foreign-currency rows), a date window allowing posting after purchase, merchant similarity and, where available, card or account hints. Each proposal SHALL carry a score and the signals that produced it.

#### Scenario: Card purchase posted later
- **WHEN** a receipt dated 3 October for CHF 84.90 at an online shop exists and a bank row of CHF -84.90 posted on 6 October mentions that merchant
- **THEN** the receipt is proposed for that row with its date, amount and merchant signals

#### Scenario: Amount alone is not enough
- **WHEN** a receipt matches a row's amount but neither the merchant nor a nearby date
- **THEN** it is not linked automatically

### Requirement: Resolve ambiguity explicitly
The system SHALL link a receipt automatically only when exactly one row and one receipt are strong mutual best matches. Otherwise it SHALL show the candidates for the user to choose or reject. A receipt SHALL be linked to at most one row unless the user splits it across shipments.

#### Scenario: Two identical orders
- **WHEN** two receipts and two bank rows share the same merchant and amount within the window
- **THEN** no automatic link is made and the user chooses the pairing

#### Scenario: Partial shipment
- **WHEN** an order receipt totals CHF 120 and two bank rows of CHF 80 and CHF 40 follow
- **THEN** both rows are offered the receipt as a candidate, and the user can link it to both

### Requirement: Receipts never change bank values
Receipt data SHALL only add evidence and suggestions. It SHALL NOT change a row's amount, date, account, transfer routing, import identity or reconciliation state.

#### Scenario: Receipt total differs from bank amount
- **WHEN** a linked receipt's total differs from the bank amount because of a discount or fee
- **THEN** the bank amount is kept, and the difference is shown and taken into account by any split suggestion

### Requirement: Refunds and payment notices
The system SHALL match refund receipts to inflow rows and SHALL treat payment or shipping notices as supporting evidence for an existing purchase match, not as separate purchases.

#### Scenario: Refund
- **WHEN** a refund receipt for CHF 25 matches a CHF +25 card row from the same merchant
- **THEN** it is proposed for that inflow row and labelled as a refund

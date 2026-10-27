# Spec Delta

## Purpose

Show where household money goes at item level by assigning a budget category to each receipt item and reporting spending by item, merchant and category over time.

## ADDED Requirements

### Requirement: Assign a category to each receipt item
Each item of a counted receipt SHALL have a category taken from the first available source: the user's choice, the linked row's split line for that item, the linked row's single category, an AI suggestion, otherwise *Uncategorized*. Each item SHALL show its category source, and categories SHALL be identified by stable ID.

#### Scenario: Item covered by a split line
- **WHEN** a receipt is linked to a row split into Groceries for "Bio Milch" and Household for "Javel"
- **THEN** "Bio Milch" is counted under Groceries and "Javel" under Household, with source *split*

#### Scenario: Row with one category
- **WHEN** a receipt with three items is linked to an unsplit row categorized as Sport
- **THEN** all three items are counted under Sport with source *row*

#### Scenario: Deleted category
- **WHEN** an item's category has been deleted in YNAB
- **THEN** the item is shown as *Uncategorized* with a note naming the deleted category

### Requirement: Categorize items with AI only on request
AI item categorization SHALL run only when the user asks for it on a set of receipts, after the estimated cost is shown. It SHALL only fill items that have no user, split or row category. Suggested category IDs SHALL be validated against the plan's live categories, and the request SHALL count against the monthly AI budget.

#### Scenario: Categorize unlinked receipts
- **WHEN** the user asks to categorize items of 10 unlinked receipts and confirms the estimate
- **THEN** their items receive AI categories with source *ai*, and items already categorized by the user, a split or a row are unchanged

#### Scenario: Unknown category from the AI
- **WHEN** the AI names a category that is not in the plan for an item
- **THEN** that item stays *Uncategorized* and the reason is recorded

### Requirement: Let the user set item categories
The user SHALL be able to set or change any item's category. A user choice SHALL take precedence over all other sources and SHALL NOT be changed by later AI requests, matching or row edits.

#### Scenario: Override an AI category
- **WHEN** the user changes an item from the AI's Groceries to Household
- **THEN** the item is counted under Household with source *user*, also after a later *Categorize items*

### Requirement: Count each purchase once in the plan currency
Spending SHALL count purchase receipts and subtract refunds. Shipping and payment notices, receipts marked *not a receipt* and receipts excluded by the user SHALL NOT be counted. Linked receipts SHALL be counted in the plan currency, with item amounts scaled so they sum to the linked bank amount. Unlinked receipts in another currency SHALL be reported separately in their own currency and not converted.

#### Scenario: Foreign receipt linked to a card row
- **WHEN** an Amazon.de receipt of EUR 26.97 is linked to a card row of CHF -25.24
- **THEN** its items are counted in CHF and sum to CHF 25.24

#### Scenario: Notices are not purchases
- **WHEN** an order has a purchase receipt, a payment notice and a shipping notice
- **THEN** only the purchase receipt is counted

#### Scenario: Unlinked foreign receipt
- **WHEN** an unlinked receipt is in USD and the plan currency is CHF
- **THEN** it appears in a separate list of amounts not included in the CHF totals

### Requirement: Report spending by item, merchant and category over time
The system SHALL report counted spending for a chosen date range grouped by category, by merchant or by item description, with totals per month. Each figure SHALL lead to the receipts and items behind it. The report SHALL state how much of the period's spending is uncategorized and how many receipts are uncounted.

#### Scenario: Category over months
- **WHEN** the user views spending by category from August to October
- **THEN** each category shows a total per month, and selecting one lists its items and receipts

#### Scenario: Search an item
- **WHEN** the user groups by item and searches "Javel"
- **THEN** matching items from all merchants are listed with their dates, amounts and categories

### Requirement: Keep spending analysis local
Spending analysis SHALL be computed from the local database and SHALL NOT send receipt data to any external service except the on-request AI item categorization.

#### Scenario: Viewing reports
- **WHEN** the user opens or changes a spending report
- **THEN** no AI request or other external call is made

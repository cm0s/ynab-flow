# transaction-classification Specification

## Purpose

Provide explainable payee and category suggestions whose confidence and review eligibility reflect the evidence available for each target.

## Requirements

### Requirement: Apply deterministic outputs without suppressing missing targets
The system SHALL preserve explicit rule outputs and continue classification for missing targets. A payee-only rule SHALL allow later category inference. Explicit ignore and review actions SHALL remain effective after later stages, and ignored rows SHALL stay outside the write queue.

#### Scenario: Payee normalization followed by category inference
- **WHEN** a matching rule supplies only a payee and historical evidence supplies a category
- **THEN** the result keeps the rule's payee, includes the inferred category and identifies the evidence for each target

#### Scenario: Explicit review action persists
- **WHEN** a rule requires review and a later stage yields high confidence
- **THEN** the row still requires review

### Requirement: Compare historical amounts and context consistently
The system SHALL compare historical and imported amounts in equivalent units and use available account and amount-sign context to distinguish historical evidence. Missing account context SHALL not be counted as positive account agreement.

#### Scenario: Equivalent amount representations
- **WHEN** an imported CHF -50.00 row is compared with history stored as -50000 milliunits
- **THEN** its amount feature is equivalent to comparing two CHF -50.00 rows

#### Scenario: Context distinguishes identical descriptions
- **WHEN** the same normalized description has different outcomes on different accounts or for opposite amount signs
- **THEN** relevant account and sign evidence influences the selected outcome and its reported confidence

### Requirement: Reflect conflicting historical outcomes
The system SHALL account for competing outcomes when estimating confidence for an exact historical description. It SHALL expose unresolved conflicts for review rather than treating a frequent winning outcome as certain solely because of its count.

#### Scenario: Three examples versus two competing examples
- **WHEN** otherwise equivalent history contains three examples of one category and two of another
- **THEN** the winning category does not receive 1.0 confidence or automatic acceptance and its explanation indicates conflicting evidence

### Requirement: Report confidence separately for each target
The system SHALL expose payee and category confidence and their provenance separately. Automatic acceptance SHALL require both required targets to be present and each confidence to reach 0.95, with no explicit review or unresolved transfer requirement. Unclassified rows SHALL require review; scores SHALL not be presented as measured accuracy.

#### Scenario: Strong payee and weak category
- **WHEN** payee confidence is 0.99 and category confidence is 0.20
- **THEN** the row requires review and its aggregate confidence cannot exceed 0.20

#### Scenario: Missing category
- **WHEN** a non-transfer row has a confident payee but no category
- **THEN** it cannot be accepted automatically

#### Scenario: Fuzzy threshold
- **WHEN** fuzzy historical confidence is below 0.75
- **THEN** category classification continues to ML or an unclassified result

### Requirement: Keep target-specific training examples aligned
The system SHALL train each prediction target using only text paired with that target's own available label. Missing payee or category labels SHALL not associate another transaction's label with the wrong memo.

#### Scenario: Partially labelled training rows
- **WHEN** training data includes category-only, payee-only and fully labelled transactions
- **THEN** each target receives only its corresponding text/label pairs and the original pairings remain intact

### Requirement: Preserve original evidence during inference
The system SHALL preserve source evidence and user overrides when reclassifying rows. Prediction explanations and normalized text SHALL remain distinguishable from original bank descriptions and outgoing annotations.

#### Scenario: Reclassification after manual correction
- **WHEN** a user edits a category and outgoing memo and reruns classification
- **THEN** the overrides remain intact while new suggestions, explanations and confidence are available separately

### Requirement: Identify categories by stable ID
The system SHALL identify categories by their YNAB category ID in training labels, historical evidence, suggestions and write-back, and SHALL resolve display names from that ID when showing them. Renaming a category in YNAB SHALL not split its history into two labels or change which category a stored suggestion refers to.

#### Scenario: Category renamed after training
- **WHEN** a category with history is renamed in YNAB and data is synced again
- **THEN** existing evidence and the trained model still predict that category, and it is shown under its new name

#### Scenario: Write-back uses the stored ID
- **WHEN** an accepted row with a suggested category is pushed
- **THEN** the outgoing transaction carries that category's ID without looking it up by name

#### Scenario: Category no longer exists
- **WHEN** a suggested category ID has been deleted in YNAB
- **THEN** the row requires review and is not pushed with that category

### Requirement: Keep weak historical evidence below auto-acceptance
The system SHALL keep fuzzy historical confidence below the auto-acceptance threshold, and SHALL keep exact historical confidence below it until at least three examples agree on the winning value. Such evidence still supplies a suggestion that the user reviews.

#### Scenario: Fuzzy match
- **WHEN** a payee or category comes from a fuzzy historical match, however similar
- **THEN** its confidence is at most 0.94 and the row requires review

#### Scenario: Two agreeing exact examples
- **WHEN** exactly two consistent historical examples match the description
- **THEN** the suggestion is made with confidence at most 0.94 and the row requires review

#### Scenario: Three agreeing exact examples
- **WHEN** three consistent same-account historical examples match and no other rule applies
- **THEN** the target can reach the auto-acceptance threshold

### Requirement: Rank evidence sources by precedence
The suggestion for each target SHALL come from the highest-ranked source that provides one, in this order: explicit user edits, rules, confident history (≥ 0.95), receipt-backed AI suggestions, other history, AI suggestions without receipt evidence, ML, and unclassified. Each target SHALL keep its own source and explanation, and replaced lower-ranked suggestions SHALL remain visible as alternatives.

#### Scenario: Receipt-backed AI over weak history
- **WHEN** history offers a 0.80 fuzzy category and an AI suggestion backed by a linked receipt offers another
- **THEN** the receipt-backed AI category is shown with source `ai`, and the history alternative remains visible in the explanation

#### Scenario: User edit wins
- **WHEN** the user has edited a row's category and AI is requested again
- **THEN** the user's category is kept and the AI result is shown only as an alternative

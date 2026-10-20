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

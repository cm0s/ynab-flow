# Spec Delta

## ADDED Requirements

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

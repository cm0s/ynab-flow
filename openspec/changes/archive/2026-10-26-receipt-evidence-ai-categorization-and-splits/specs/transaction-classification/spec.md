# Spec Delta

## ADDED Requirements

### Requirement: Rank evidence sources by precedence
The suggestion for each target SHALL come from the highest-ranked source that provides one, in this order: explicit user edits, rules, confident history (≥ 0.95), receipt-backed AI suggestions, other history, AI suggestions without receipt evidence, ML, and unclassified. Each target SHALL keep its own source and explanation, and replaced lower-ranked suggestions SHALL remain visible as alternatives.

#### Scenario: Receipt-backed AI over weak history
- **WHEN** history offers a 0.80 fuzzy category and an AI suggestion backed by a linked receipt offers another
- **THEN** the receipt-backed AI category is shown with source `ai`, and the history alternative remains visible in the explanation

#### Scenario: User edit wins
- **WHEN** the user has edited a row's category and AI is requested again
- **THEN** the user's category is kept and the AI result is shown only as an alternative

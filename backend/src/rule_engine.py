"""
FR-4: Deterministic Rule Engine

Evaluates normalized transaction memos against user-defined rules
to assign payee, category, or flags.
"""

import re
from dataclasses import dataclass
from typing import Optional, List
from sqlalchemy.orm import Session
from src.models import Rule


@dataclass
class RuleMatch:
    """Result of a successful rule match (FR-4.2 / FR-4.4)."""
    rule_name: str
    assign_payee: Optional[str] = None
    assign_category: Optional[str] = None
    flag_review: bool = False
    flag_ignore: bool = False


class RuleEngine:
    """
    Loads rules from the database sorted by priority (descending).
    The first matching rule wins (FR-4.3).
    """

    def __init__(self, db: Session, plan_id: str):
        self.rules: List[Rule] = (
            db.query(Rule)
            .filter(Rule.plan_id == plan_id)
            .order_by(Rule.priority.desc())
            .all()
        )

    def evaluate(
        self,
        normalized_memo: str,
        amount: float = 0.0,
        account_name: str = "",
        category_name: str = "",
    ) -> Optional[RuleMatch]:
        """
        Evaluate the normalized memo against all rules.
        Returns the first RuleMatch or None.
        """
        for rule in self.rules:
            if self._matches(rule, normalized_memo, amount, account_name, category_name):
                return RuleMatch(
                    rule_name=rule.name,
                    assign_payee=rule.assign_payee,
                    assign_category=rule.assign_category,
                    flag_review=rule.flag_review,
                    flag_ignore=rule.flag_ignore,
                )
        return None

    # ------------------------------------------------------------------
    # Internal matching logic
    # ------------------------------------------------------------------

    def _matches(
        self,
        rule: Rule,
        memo: str,
        amount: float,
        account_name: str,
        category_name: str,
    ) -> bool:
        """Check whether ALL conditions on a rule are satisfied."""

        # 1. Pattern match (FR-4.1: exact | contains | regex)
        if not self._pattern_matches(rule, memo):
            return False

        # 2. Amount sign filter
        if rule.amount_sign:
            if rule.amount_sign == "positive" and amount < 0:
                return False
            if rule.amount_sign == "negative" and amount >= 0:
                return False

        # 3. Amount range filter
        if rule.amount_min is not None and abs(amount) < rule.amount_min:
            return False
        if rule.amount_max is not None and abs(amount) > rule.amount_max:
            return False

        # 4. Account filter (substring)
        if rule.account_filter:
            if rule.account_filter.upper() not in account_name.upper():
                return False

        # 5. Category filter (substring)
        if rule.category_filter:
            if rule.category_filter.upper() not in category_name.upper():
                return False

        return True

    @staticmethod
    def _pattern_matches(rule: Rule, memo: str) -> bool:
        """Apply the pattern according to match_type."""
        pattern = rule.pattern.upper()
        memo_upper = memo.upper()

        if rule.match_type == "exact":
            return memo_upper == pattern
        elif rule.match_type == "contains":
            return pattern in memo_upper
        elif rule.match_type == "regex":
            try:
                return bool(re.search(rule.pattern, memo, re.IGNORECASE))
            except re.error:
                return False
        return False

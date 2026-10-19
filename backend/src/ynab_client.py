import os
import httpx
from typing import Optional, Dict, Any

class YNABAPIError(Exception):
    """YNAB API failure. ``status_code`` is set for HTTP error responses."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

class YnabClient:
    def __init__(self, api_key: str):
        self.base_url = "https://api.ynab.com/v1"
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
        self.client = httpx.Client(headers=self.headers, base_url=self.base_url, timeout=60.0)

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.client.get(endpoint, params=params)
        
        if response.status_code >= 400:
            error_detail = response.text
            try:
                error_detail = response.json().get("error", {}).get("detail", error_detail)
            except Exception:
                pass
            raise YNABAPIError(f"YNAB API Error ({response.status_code}): {error_detail}", response.status_code)
            
        return response.json().get("data", {})

    def get_budgets(self) -> Dict[str, Any]:
        """Fetch all budgets (plans) for the authenticated user."""
        return self._get("/budgets", params={"include_accounts": "true"})

    def get_accounts(self, budget_id: str, last_knowledge_of_server: Optional[int] = None) -> Dict[str, Any]:
        """Fetch accounts for a specific budget, supporting delta syncs."""
        params = {}
        if last_knowledge_of_server is not None:
             params["last_knowledge_of_server"] = last_knowledge_of_server
        return self._get(f"/budgets/{budget_id}/accounts", params=params)

    def get_categories(self, budget_id: str, last_knowledge_of_server: Optional[int] = None) -> Dict[str, Any]:
        """Fetch category groups and categories, supporting delta syncs."""
        params = {}
        if last_knowledge_of_server is not None:
             params["last_knowledge_of_server"] = last_knowledge_of_server
        return self._get(f"/budgets/{budget_id}/categories", params=params)

    def get_payees(self, budget_id: str, last_knowledge_of_server: Optional[int] = None) -> Dict[str, Any]:
        """Fetch payees for a specific budget, supporting delta syncs."""
        params = {}
        if last_knowledge_of_server is not None:
             params["last_knowledge_of_server"] = last_knowledge_of_server
        return self._get(f"/budgets/{budget_id}/payees", params=params)

    def get_transactions(self, budget_id: str, last_knowledge_of_server: Optional[int] = None) -> Dict[str, Any]:
        """Fetch all transactions for a specific budget, supporting delta syncs."""
        params = {}
        if last_knowledge_of_server is not None:
             params["last_knowledge_of_server"] = last_knowledge_of_server
        return self._get(f"/budgets/{budget_id}/transactions", params=params)

    def get_transaction(self, budget_id: str, transaction_id: str) -> Dict[str, Any]:
        """Fetch a single transaction (used to resolve ID-only create responses)."""
        return self._get(f"/budgets/{budget_id}/transactions/{transaction_id}")

    def _post(self, endpoint: str, json_body: Dict[str, Any]) -> Dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)
        txn_count = len(json_body.get("transactions", []))
        logger.info("POST %s (%d transactions)", endpoint, txn_count)
        response = self.client.post(endpoint, json=json_body)
        logger.info("POST %s completed: %d in %.1fs", endpoint, response.status_code, response.elapsed.total_seconds())

        if response.status_code >= 400:
            error_detail = response.text
            try:
                error_detail = response.json().get("error", {}).get("detail", error_detail)
            except Exception:
                pass
            raise YNABAPIError(f"YNAB API Error ({response.status_code}): {error_detail}", response.status_code)

        return response.json().get("data", {})

    def create_transactions(self, budget_id: str, transactions: list) -> Dict[str, Any]:
        """
        Create one or more transactions in YNAB (FR-9.2).
        
        Each transaction dict should have:
          account_id, date, amount (integer milliunits), payee_id or payee_name,
          category_id (nullable), memo, cleared, approved, import_id (<= 36 chars).

        The response data holds ``transaction_ids``, ``transactions`` (objects with
        ``id`` and ``import_id``) and ``duplicate_import_ids``; callers must correlate by
        import_id, never by position.
        """
        return self._post(
            f"/budgets/{budget_id}/transactions",
            {"transactions": transactions},
        )

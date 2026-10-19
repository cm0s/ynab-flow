from typing import List, Optional
from sqlalchemy.orm import Session
from src.ynab_client import YnabClient
from src.models import Plan, Account, CategoryGroup, Category, Payee, Transaction


def _int_milliunits(value):
    """YNAB amounts are integer milliunits; reject anything else."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None

class SyncService:
    def __init__(self, db: Session, ynab_client: YnabClient):
        self.db = db
        self.client = ynab_client

    def sync_budgets(self):
        """Fetches budgets and accounts, upserting them into the local database"""
        data = self.client.get_budgets()
        budgets = data.get("budgets", [])
        
        for budget_data in budgets:
            ynab_plan_id = budget_data.get("id")
            
            # Upsert Plan
            plan = self.db.query(Plan).filter(Plan.ynab_plan_id == ynab_plan_id).first()
            if not plan:
                plan = Plan(
                    ynab_plan_id=ynab_plan_id,
                    name=budget_data.get("name"),
                )
                self.db.add(plan)
                self.db.flush() # flush to get plan.id
            else:
                plan.name = budget_data.get("name")
                
            # Upsert Accounts included in the budget payload
            for account_data in budget_data.get("accounts", []):
                ynab_account_id = account_data.get("id")
                account = self.db.query(Account).filter(Account.ynab_account_id == ynab_account_id).first()
                if not account:
                    account = Account(
                        plan_id=plan.id,
                        ynab_account_id=ynab_account_id,
                        name=account_data.get("name"),
                        type=account_data.get("type"),
                        closed=account_data.get("closed"),
                        currency=budget_data.get("currency_format", {}).get("iso_code")
                    )
                    self.db.add(account)
                else:
                    account.name = account_data.get("name")
                    account.closed = account_data.get("closed")
                    
        self.db.commit()

    def sync_categories(self, plan: Plan):
        """Fetches category groups and categories, using delta sync if available."""
        server_knowledge = int(plan.last_server_knowledge) if plan.last_server_knowledge else 0
        
        data = self.client.get_categories(plan.ynab_plan_id, last_knowledge_of_server=server_knowledge)
        groups = data.get("category_groups", [])
        
        for group_data in groups:
            ynab_group_id = group_data.get("id")
            
            group = self.db.query(CategoryGroup).filter(CategoryGroup.ynab_category_group_id == ynab_group_id).first()
            if not group:
                group = CategoryGroup(
                    plan_id=plan.id,
                    ynab_category_group_id=ynab_group_id,
                    name=group_data.get("name"),
                    deleted=group_data.get("deleted", False)
                )
                self.db.add(group)
                self.db.flush()
            else:
                group.name = group_data.get("name")
                group.deleted = group_data.get("deleted", False)
                
            for cat_data in group_data.get("categories", []):
                ynab_cat_id = cat_data.get("id")
                category = self.db.query(Category).filter(Category.ynab_category_id == ynab_cat_id).first()
                if not category:
                    category = Category(
                        category_group_id=group.id,
                        ynab_category_id=ynab_cat_id,
                        name=cat_data.get("name"),
                        deleted=cat_data.get("deleted", False)
                    )
                    self.db.add(category)
                else:
                    category.name = cat_data.get("name")
                    category.deleted = cat_data.get("deleted", False)
                    
        # Update server knowledge safely
        new_knowledge = data.get("server_knowledge")
        if new_knowledge:
            plan.last_server_knowledge = str(new_knowledge)
            
        self.db.commit()

    def sync_payees(self, plan: Plan):
         """Fetches payees, using delta sync"""
         server_knowledge = int(plan.last_knowledge_payees) if plan.last_knowledge_payees else 0
         data = self.client.get_payees(plan.ynab_plan_id, last_knowledge_of_server=server_knowledge)
         
         payees = {p.ynab_payee_id: p for p in self.db.query(Payee).filter(Payee.plan_id == plan.id).all()}
         for payee_data in data.get("payees", []):
             ynab_payee_id = payee_data.get("id")
             payee = payees.get(ynab_payee_id)
             
             if not payee:
                 payee = Payee(
                     plan_id=plan.id,
                     ynab_payee_id=ynab_payee_id,
                     name=payee_data.get("name"),
                     transfer_account_id=payee_data.get("transfer_account_id"),
                     deleted=payee_data.get("deleted", False)
                 )
                 self.db.add(payee)
                 payees[ynab_payee_id] = payee
             else:
                 payee.name = payee_data.get("name")
                 payee.transfer_account_id = payee_data.get("transfer_account_id")
                 payee.deleted = payee_data.get("deleted", False)
                 
         new_knowledge = data.get("server_knowledge")
         if new_knowledge:
             plan.last_knowledge_payees = str(new_knowledge)

         self.db.commit()

    def sync_transactions(self, plan: Plan):
        """Fetches all transactions using delta sync"""
        server_knowledge = int(plan.last_knowledge_transactions) if plan.last_knowledge_transactions else 0
        data = self.client.get_transactions(plan.ynab_plan_id, last_knowledge_of_server=server_knowledge)

        # Cache references to avoid remote database round trips for every row.
        accounts = {a.ynab_account_id: a.id for a in self.db.query(Account).filter(Account.plan_id == plan.id).all()}
        payees = {p.ynab_payee_id: p.id for p in self.db.query(Payee).filter(Payee.plan_id == plan.id).all()}
        categories = {c.ynab_category_id: c.id for c in self.db.query(Category).join(CategoryGroup).filter(CategoryGroup.plan_id == plan.id).all()}
        transactions = {t.ynab_transaction_id: t for t in self.db.query(Transaction).filter(Transaction.plan_id == plan.id).all()}

        for txn_data in data.get("transactions", []):
             ynab_txn_id = txn_data.get("id")
             
             account_id = accounts.get(txn_data.get("account_id"))
             if not account_id:
                 continue  # Data inconsistency or skipped account
             payee_id = payees.get(txn_data.get("payee_id"))
             category_id = categories.get(txn_data.get("category_id"))
             txn = transactions.get(ynab_txn_id)

             if not txn:
                  txn = Transaction(
                      plan_id=plan.id,
                      account_id=account_id,
                      payee_id=payee_id,
                      category_id=category_id,
                      ynab_transaction_id=ynab_txn_id,
                      date=txn_data.get("date"),
                      amount=txn_data.get("amount"),
                      amount_milliunits=_int_milliunits(txn_data.get("amount")),
                      import_id=txn_data.get("import_id"),
                      transfer_account_ynab_id=txn_data.get("transfer_account_id"),
                      transfer_transaction_ynab_id=txn_data.get("transfer_transaction_id"),
                      memo=txn_data.get("memo"),
                      cleared=txn_data.get("cleared"),
                      approved=txn_data.get("approved", False),
                      deleted=txn_data.get("deleted", False)
                  )
                  self.db.add(txn)
                  transactions[ynab_txn_id] = txn
             else:
                  txn.account_id = account_id
                  txn.payee_id = payee_id
                  txn.category_id = category_id
                  txn.date = txn_data.get("date")
                  txn.amount = txn_data.get("amount")
                  txn.amount_milliunits = _int_milliunits(txn_data.get("amount"))
                  txn.import_id = txn_data.get("import_id")
                  txn.transfer_account_ynab_id = txn_data.get("transfer_account_id")
                  txn.transfer_transaction_ynab_id = txn_data.get("transfer_transaction_id")
                  txn.memo = txn_data.get("memo")
                  txn.cleared = txn_data.get("cleared")
                  txn.approved = txn_data.get("approved", False)
                  txn.deleted = txn_data.get("deleted", False)
                  
        new_knowledge = data.get("server_knowledge")
        if new_knowledge:
             plan.last_knowledge_transactions = str(new_knowledge)

        self.db.commit()

    def sync_all(self, plan: Plan):
        """Orchestrates syncing all data types for a given plan."""
        # Always sync structural entities first
        self.sync_categories(plan)
        self.sync_payees(plan)
        # Finally sync transactions which rely on structural FKs
        self.sync_transactions(plan)

from typing import List, Optional
from sqlalchemy.orm import Session
from src.ynab_client import YnabClient
from src.models import Plan, Account, CategoryGroup, Category, Payee, Transaction

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
         
         for payee_data in data.get("payees", []):
             ynab_payee_id = payee_data.get("id")
             payee = self.db.query(Payee).filter(Payee.ynab_payee_id == ynab_payee_id).first()
             
             if not payee:
                 payee = Payee(
                     plan_id=plan.id,
                     ynab_payee_id=ynab_payee_id,
                     name=payee_data.get("name"),
                     deleted=payee_data.get("deleted", False)
                 )
                 self.db.add(payee)
             else:
                 payee.name = payee_data.get("name")
                 payee.deleted = payee_data.get("deleted", False)
                 
         new_knowledge = data.get("server_knowledge")
         if new_knowledge:
             plan.last_knowledge_payees = str(new_knowledge)

         self.db.commit()

    def sync_transactions(self, plan: Plan):
        """Fetches all transactions using delta sync"""
        server_knowledge = int(plan.last_knowledge_transactions) if plan.last_knowledge_transactions else 0
        data = self.client.get_transactions(plan.ynab_plan_id, last_knowledge_of_server=server_knowledge)

        for txn_data in data.get("transactions", []):
             ynab_txn_id = txn_data.get("id")
             
             # Resolve internal FKs
             ynab_account_id = txn_data.get("account_id")
             account = self.db.query(Account).filter(Account.ynab_account_id == ynab_account_id).first()
             if not account:
                 continue # Data inconsistency or skipped account
                 
             ynab_payee_id = txn_data.get("payee_id")
             payee_id = None
             if ynab_payee_id:
                  payee = self.db.query(Payee).filter(Payee.ynab_payee_id == ynab_payee_id).first()
                  payee_id = payee.id if payee else None
                  
             ynab_category_id = txn_data.get("category_id")
             category_id = None
             if ynab_category_id:
                  category = self.db.query(Category).filter(Category.ynab_category_id == ynab_category_id).first()
                  category_id = category.id if category else None

             txn = self.db.query(Transaction).filter(Transaction.ynab_transaction_id == ynab_txn_id).first()
             
             if not txn:
                  txn = Transaction(
                      plan_id=plan.id,
                      account_id=account.id,
                      payee_id=payee_id,
                      category_id=category_id,
                      ynab_transaction_id=ynab_txn_id,
                      date=txn_data.get("date"),
                      amount=txn_data.get("amount"),
                      memo=txn_data.get("memo"),
                      cleared=txn_data.get("cleared"),
                      approved=txn_data.get("approved", False),
                      deleted=txn_data.get("deleted", False)
                  )
                  self.db.add(txn)
             else:
                  txn.account_id = account.id
                  txn.payee_id = payee_id
                  txn.category_id = category_id
                  txn.date = txn_data.get("date")
                  txn.amount = txn_data.get("amount")
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

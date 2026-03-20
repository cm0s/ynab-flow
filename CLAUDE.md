# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YNAB Flow is a local-first web app that ingests bank CSVs, syncs historical YNAB transactions, and predicts the best Payee and Category for each new transaction using a multi-layer classification pipeline.

## Commands

### Backend
```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt
fastapi dev src/main.py              # Dev server on :8000, docs at /docs
pytest tests/ -v                     # Run all tests (31+)
pytest tests/test_normalizer.py -v   # Run single test file
```

### Frontend
```bash
cd frontend
pnpm install
pnpm run dev       # Vite dev server on :5173, proxies /api → :8000
pnpm run build     # TypeScript check + production build
pnpm run lint      # ESLint
```

Both backend and frontend must run simultaneously for development. The Vite config proxies `/api/*` requests to `http://127.0.0.1:8000` (stripping the `/api` prefix).

### Database Migrations
Alembic manages SQLite migrations in `backend/alembic/`. The database file is `backend/ynab_flow.db`.

## Architecture

### Backend (FastAPI + SQLAlchemy + SQLite)

**Classification Pipeline** — The orchestrator (`orchestrator.py`) chains these layers in precedence order:
1. **Deterministic Rules** (`rule_engine.py`) — user-defined pattern-matching rules, confidence 1.0
2. **Exact Historical Match** (`historical_matcher.py`) — identical normalized memo lookup, confidence ~0.9–1.0
3. **Fuzzy Historical Match** (`historical_matcher.py`) — SequenceMatcher similarity with clustering, confidence ~0.6–0.9
4. **ML Prediction** (`ml_classifier.py`) — TF-IDF + LogisticRegression trained on historical memos, models persisted as joblib in `backend/models/`
5. **Unclassified** fallback

**Confidence thresholds:** ≥0.95 auto-approve, ≥0.75 flagged for review, <0.75 requires review.

**Key services:**
- `sync_service.py` — delta sync from YNAB API using per-entity `last_server_knowledge` tracking
- `ynab_client.py` — httpx wrapper around YNAB v1 API
- `normalizer.py` — strips bank boilerplate (TWINT, ACHAT, dates, IBANs) and extracts merchant stems
- `csv_service.py` — parses bank CSVs and runs bulk predictions
- `write_back_service.py` — pushes transactions to YNAB with dry_run/create/create_or_skip modes
- `transfer_detector.py` — heuristic detection of inter-account transfers

**ORM models** (`models.py`): Plan, Account, Category, CategoryGroup, Payee, Transaction, Rule.

### Frontend (React 19 + TypeScript + Vite)

**State management:** TanStack Query (React Query) for server state, React useState for UI state.

**View flow:** import → review → push → settings → dashboard (state machine in `App.tsx`).

**Key components:**
- `FileDrop` — drag-and-drop CSV upload
- `ReviewTable` — interactive review with inline editing, filtering, bulk actions
- `SettingsPage` / `RulesManager` — rule CRUD, Full Sync, Train ML triggers
- `MetricsDashboard` — classification stats
- `ResultsTable` — write-back results display

**API client** (`api/client.ts`): centralized fetch wrapper; all backend calls go through here.

## Testing

Backend tests use pytest with respx for HTTP mocking. Test files mirror source files: `test_normalizer.py`, `test_rule_engine.py`, `test_historical_matcher.py`, `test_orchestrator.py`, `test_csv_service.py`, `test_sync.py`, `test_transfer_detector.py`, `test_main.py`.

## Workflow

Always commit changes after finishing a task, but always ask the user for confirmation before committing changes. Never commit automatically after completing a task.

## Environment

`backend/.env` contains `YNAB_API_KEY` (loaded via python-dotenv). Never commit this file.

# CLAUDE.md

## Rules
- Ask user for confirmation before committing. Never commit automatically.
- Never commit `backend/.env` (contains `YNAB_API_KEY`).
- Always run the full test suite (`pytest tests/ -v` and `pnpm run build`) after finishing a task.

## Commands

Backend: `cd backend && source venv/bin/activate`
- `fastapi dev src/main.py` — dev server :8000, docs at /docs
- `pytest tests/ -v` — all tests; `pytest tests/test_<module>.py -v` for one file

Frontend: `cd frontend`
- `pnpm run dev` — Vite :5173, proxies `/api/*` → :8000 (strips prefix)
- `pnpm run build` — TS check + production build
- `pnpm run lint`

Both servers must run simultaneously. DB migrations: Alembic in `backend/alembic/`, DB at `backend/ynab_flow.db`.

## Architecture

Local-first app: ingest bank CSVs, sync YNAB history, predict Payee/Category via multi-layer pipeline.

**Classification pipeline** (precedence order in `orchestrator.py`):
1. Rules (`rule_engine.py`) — deterministic patterns, confidence 1.0
2. Exact history (`historical_matcher.py`) — normalized memo lookup, ~0.9–1.0
3. Fuzzy history (`historical_matcher.py`) — SequenceMatcher + clustering, ~0.6–0.9
4. ML (`ml_classifier.py`) — TF-IDF + LogReg, models in `backend/models/`
5. Unclassified fallback

**Thresholds:** ≥0.95 auto-approve, ≥0.75 flagged, <0.75 requires review.

**Frontend:** React 19 + TanStack Query. View flow: import → review → push → settings → dashboard (`App.tsx`).

**Testing:** pytest + respx. Test files mirror source: `test_<module>.py`.

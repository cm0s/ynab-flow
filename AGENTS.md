# AGENTS.md

## Rules
- After completing a feature, bug fix, or similar task, propose making a commit and wait for the user to say whether they want to commit the changes. Never commit automatically.
- Never commit `backend/.env` (contains `YNAB_API_KEY`).
- Always run the full test suite (`pytest tests/ -v` and `pnpm run build`) after finishing a task.

## Commands

Backend: `cd backend && source venv/bin/activate`
- `fastapi dev src/main.py` — dev server :8000, docs at /docs
- `alembic upgrade head` — apply database migrations before starting a new installation
- `pytest tests/ -v` — all tests; `pytest tests/test_<module>.py -v` for one file

Frontend: `cd frontend`
- `pnpm run dev` — Vite :5173, proxies `/api/*` → :8000 (strips prefix)
- `pnpm run build` — TS check + production build
- `pnpm run lint`

Both servers must run simultaneously during local development. DB migrations: Alembic in `backend/alembic/`. SQLite defaults to `backend/ynab_flow.db` when commands run from `backend/`; set `DATABASE_URL` to use PostgreSQL.

## Deployment

- Hosted setup: GitHub Pages frontend, Render backend, Supabase PostgreSQL and Auth. See `README.md` for setup steps and `render.yaml` for backend configuration.
- Render runs migrations before starting Uvicorn. Hosted authentication requires `SUPABASE_URL`, `ADMIN_USER_ID`, and `AUTH_REQUIRED=true` (also enforced automatically on Render). Access is limited to the configured admin user. Legacy HS256 signing additionally requires `SUPABASE_JWT_SECRET`.
- Set `ALLOWED_ORIGINS` to the frontend origin, without a path or trailing slash.
- Production frontend builds require `VITE_API_URL`, `VITE_SUPABASE_URL`, and `VITE_SUPABASE_ANON_KEY`. The last value is a public anon/publishable key. Never expose backend secrets through `VITE_` variables.
- `VITE_BASE_PATH` sets the frontend base path; the Pages workflow uses `/ynab-flow/` and deploys on pushes to `main` or manual dispatch.
- Local development can run without Supabase authentication. Environment examples are in `backend/.env.example` and `frontend/.env.example`.
- For build checks without hosted services, supply placeholder connection values; this verifies compilation, not live connectivity:
  `VITE_API_URL=https://api.example.com VITE_SUPABASE_URL=https://test.supabase.co VITE_SUPABASE_ANON_KEY=test-public-key VITE_BASE_PATH=/ynab-flow/ pnpm run build`

## Architecture

Local-first app: ingest bank CSVs, sync YNAB history, predict Payee/Category via multi-layer pipeline.

**Classification pipeline** (precedence order in `orchestrator.py`):
1. Rules (`rule_engine.py`) — deterministic patterns, confidence 1.0
2. Exact history (`historical_matcher.py`) — normalized memo lookup, ~0.9–1.0
3. Fuzzy history (`historical_matcher.py`) — SequenceMatcher + clustering, ~0.6–0.9
4. ML (`ml_classifier.py`) — TF-IDF + LogReg, models in `backend/models/`
5. Unclassified fallback

**Thresholds:** History/ML predictions ≥0.95 are eligible for auto-approval; predictions below 0.95 require review. The UI shows medium confidence for 0.75–<0.95 and low confidence below 0.75. Fuzzy history must reach 0.75 to be used before ML. Rules have confidence 1.0 but can explicitly require review or mark a row ignored.

**Frontend:** React 19 + TanStack Query. View flow: import → review → push → settings → dashboard (`App.tsx`).

**Testing:** pytest + respx. Test files mirror source: `test_<module>.py`.

# YNAB Flow

A local-first web application that ingests bank CSVs, syncs historical YNAB transactions, learns from prior assignments, and predicts the best Payee and Category for each new transaction.

## Getting Started

This application consists of two parts: a Python backend and a React frontend. Both need to be running simultaneously during development.

### 1. Running the Backend

The backend is built with FastAPI and uses SQLite.

```bash
cd backend

# Activate the virtual environment
source venv/bin/activate

# Install the backend dependencies
pip install -r requirements.txt

# Start the development server
fastapi dev src/main.py
```

The backend API will be available at `http://localhost:8000`. You can view the API documentation at `http://localhost:8000/docs`.

### 2. Running the Frontend

The frontend is a React application built with Vite and TypeScript. Open a **new terminal window** to run the frontend alongside the backend.

```bash
cd frontend

# Install dependencies (only required the first time)
pnpm install

# Start the Vite development server
pnpm run dev
```

The frontend application will be available at `http://localhost:5173`.

## Deploy: GitHub Pages + Render + Supabase

The hosted app uses one shared database and allows one Supabase Auth user.

1. Create a Supabase project. In Authentication, create your user with an email
   and password, confirm the email, and disable public signups. Copy the user's
   UUID for `ADMIN_USER_ID`. Copy the project URL and public anon/publishable key
   from the project's API settings.
2. In Supabase's Connect dialog, obtain the PostgreSQL **session pooler** connection
   string (IPv4 compatible). URL-encode special characters in the password and
   append `?sslmode=require`. This is `DATABASE_URL`; it is a backend secret.
   The app connects directly with SQLAlchemy, so use a dedicated Supabase project.
3. In Render, create a Blueprint from this GitHub repository using `render.yaml`.
   Enter `DATABASE_URL`, `YNAB_API_KEY`, `SUPABASE_URL`, `ADMIN_USER_ID`, and
   `ALLOWED_ORIGINS=https://YOUR_GITHUB_USERNAME.github.io` (origin only, no
   `/ynab-flow` path or trailing slash). The Blueprint pins Python 3.12, installs
   dependencies, runs `alembic upgrade head`, then binds Uvicorn to Render's port.
   For a legacy HS256 Supabase project, also add `SUPABASE_JWT_SECRET` in Render;
   current ES256/RS256 projects use public signing keys automatically.
4. In GitHub repository Settings → Secrets and variables → Actions, add:
   - `VITE_API_URL`: your Render service URL, e.g. `https://ynab-flow.onrender.com`
   - `VITE_SUPABASE_URL`: the same Supabase project URL
   - `VITE_SUPABASE_ANON_KEY`: the public anon or publishable key
   Never put the YNAB key, database password, JWT secret, or service-role key in
   a `VITE_` variable; frontend configuration is public.
5. In GitHub Settings → Pages, select **GitHub Actions** as the source. Run
   **Deploy Frontend to GitHub Pages** from Actions (or push to `main`). The
   workflow uses `/ynab-flow/`; adjust `VITE_BASE_PATH` there if the repository
   name differs or you use a custom domain. Configuration changes require a new
   build. Set Supabase Auth's Site URL to your complete Pages URL.
6. Verify Render's `/health` returns `{"status":"ok"}` and `/plans` without a
   token returns 401. Open Pages, sign in, click **Sync Budgets**, select a plan,
   and sync its history. Import a small CSV and reload to verify persistence.
   Test a non-admin account: API requests should return 403. Pushing approved
   transactions writes to YNAB, so only do that when ready.

Render's free service can sleep and lose local files on restart. PostgreSQL keeps
import/review data; ML models are rebuilt from synced history at startup, or via
**Train** in Settings after the initial history sync. A paid service may be useful
for predictable startup times. No local SQLite database is uploaded automatically;
sync YNAB history in the hosted app and recreate any local custom rules.

For a local production build, copy `frontend/.env.example` to
`frontend/.env.local` and fill in the three `VITE_` connection values, then run
`pnpm run build`. Production builds fail if any are missing. `pnpm run dev` still
supports local development without Supabase. For a new local backend database,
run `alembic upgrade head` before starting the API.

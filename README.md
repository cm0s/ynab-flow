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

# Start the development server
uvicorn src.main:app --reload
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

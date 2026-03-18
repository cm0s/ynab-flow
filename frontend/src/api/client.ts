/** API client — all calls go through the Vite proxy at /api */

const BASE = '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

/* ---- Plans ---- */
export interface Plan {
  id: string;
  name: string;
  ynab_plan_id: string;
}

export const fetchPlans = () => request<Plan[]>('/plans');

/* ---- Upload CSV ---- */
export interface PredictionRow {
  row_index: number;
  date: string;
  original_memo: string;
  cleaned_memo: string;
  merchant_stem: string;
  amount: number;
  label: string;
  source_category: string;
  payee: string | null;
  category: string | null;
  confidence: number;
  source: string;
  explanation: string;
  review_required: boolean;
  flag_ignore: boolean;
}

export interface UploadResult {
  count: number;
  predictions: PredictionRow[];
}

export async function uploadCSV(planId: string, file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE}/upload-csv?plan_id=${planId}`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Upload error ${res.status}`);
  }
  return res.json();
}

/* ---- Sync ---- */
export const syncBudgets = () =>
  request<{ status: string }>('/sync/budgets', { method: 'POST' });

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

/* ---- Accounts ---- */
export interface Account {
  id: string;
  name: string;
  type: string;
  closed: boolean;
}

export const fetchAccounts = (planId: string) =>
  request<Account[]>(`/plans/${planId}/accounts`);

/* ---- Write-Back ---- */
export interface WriteBackTransaction {
  date: string;
  amount: number;
  payee_name: string;
  category_name: string;
  memo: string;
  account_id: string;
  cleared?: string;
}

export interface WriteBackResultItem {
  index: number;
  date: string;
  payee_name: string;
  amount: number;
  status: string;
  ynab_transaction_id: string | null;
  error: string | null;
  import_id: string | null;
}

export interface WriteBackResponse {
  mode: string;
  total: number;
  created: number;
  skipped: number;
  errors: number;
  results: WriteBackResultItem[];
}

export function writeBack(
  planId: string,
  mode: string,
  transactions: WriteBackTransaction[],
): Promise<WriteBackResponse> {
  return request<WriteBackResponse>('/write-back', {
    method: 'POST',
    body: JSON.stringify({ plan_id: planId, mode, transactions }),
  });
}

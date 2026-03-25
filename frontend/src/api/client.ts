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

export interface ImportRowData extends PredictionRow {
  id: string;
  status: string;
  edited_payee: string;
  edited_category: string;
}

export interface UploadResult {
  batch_id?: string;
  count: number;
  predictions: ImportRowData[];
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

export const syncPlanData = (planId: string, full = false) =>
  request<{ status: string }>(`/sync/plan/${planId}${full ? '?full=true' : ''}`, { method: 'POST' });

export const trainModels = (planId: string) =>
  request<{ status: string }>(`/train?plan_id=${planId}`, { method: 'POST' });

/* ---- Reclassify ---- */
export function reclassify(planId: string, batchId?: string): Promise<UploadResult> {
  return request<UploadResult>('/reclassify', {
    method: 'POST',
    body: JSON.stringify({ plan_id: planId, batch_id: batchId }),
  });
}

/* ---- Import Batches ---- */
export interface ActiveBatchResponse {
  batch_id: string;
  filename: string | null;
  created_at: string | null;
  rows: ImportRowData[];
}

export const fetchActiveBatch = (planId: string) =>
  request<ActiveBatchResponse>(`/import-batches/${planId}/active`);

export interface ImportRowUpdate {
  id: string;
  status?: string;
  edited_payee?: string;
  edited_category?: string;
}

export function updateImportRows(updates: ImportRowUpdate[]) {
  return request<{ updated: number }>('/import-rows', {
    method: 'PATCH',
    body: JSON.stringify({ updates }),
  });
}

export function completeBatch(batchId: string) {
  return request<{ status: string }>(`/import-batches/${batchId}/complete`, { method: 'POST' });
}

/* ---- Accounts ---- */
export interface Account {
  id: string;
  name: string;
  type: string;
  closed: boolean;
}

export const fetchAccounts = (planId: string) =>
  request<Account[]>(`/plans/${planId}/accounts`);

/* ---- Categories ---- */
export interface CategoryGroup {
  id: string;
  name: string;
  categories: { id: string; name: string }[];
}

export const fetchCategories = (planId: string) =>
  request<CategoryGroup[]>(`/plans/${planId}/categories`);

/* ---- Payees ---- */
export interface PayeeItem {
  id: string;
  name: string;
}

export const fetchPayees = (planId: string) =>
  request<PayeeItem[]>(`/plans/${planId}/payees`);

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
  batchId?: string,
): Promise<WriteBackResponse> {
  return request<WriteBackResponse>('/write-back', {
    method: 'POST',
    body: JSON.stringify({ plan_id: planId, mode, transactions, batch_id: batchId }),
  });
}

/* ---- Rules ---- */
export interface Rule {
  id: string;
  name: string;
  priority: number;
  match_type: string;
  pattern: string;
  assign_payee: string | null;
  assign_category: string | null;
  amount_sign: string | null;
  amount_min: number | null;
  amount_max: number | null;
  account_filter: string | null;
  category_filter: string | null;
  flag_review: boolean;
  flag_ignore: boolean;
}

export const fetchRules = (planId: string) =>
  request<Rule[]>(`/rules?plan_id=${planId}`);

export function createRule(rule: {
  plan_id: string;
  name: string;
  priority?: number;
  match_type: string;
  pattern: string;
  assign_payee?: string;
  assign_category?: string;
}) {
  return request<{ status: string; rule_id: string }>('/rules', {
    method: 'POST',
    body: JSON.stringify({ priority: 0, flag_review: false, flag_ignore: false, ...rule }),
  });
}

export function updateRule(ruleId: string, rule: {
  plan_id: string;
  name: string;
  priority?: number;
  match_type: string;
  pattern: string;
  assign_payee?: string;
  assign_category?: string;
}) {
  return request<{ status: string; rule_id: string }>(`/rules/${ruleId}`, {
    method: 'PUT',
    body: JSON.stringify({ priority: 0, flag_review: false, flag_ignore: false, ...rule }),
  });
}

export function deleteRule(ruleId: string) {
  return request<{ status: string }>(`/rules/${ruleId}`, { method: 'DELETE' });
}

/* ---- Health ---- */
export const healthCheck = () => request<{ status: string }>('/health');

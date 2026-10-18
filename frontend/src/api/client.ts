/** API client — uses Vite proxy in dev, direct URL in production */

import { supabase } from '../lib/supabase';

const BASE = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '');

async function getAuthHeaders(): Promise<Record<string, string>> {
  if (!supabase) return {};
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...authHeaders, ...init?.headers },
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
  edited_memo: string;
  flag_color: string;
}

export interface UploadResult {
  batch_id?: string;
  count: number;
  predictions: ImportRowData[];
}

export async function uploadCSV(planId: string, file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE}/upload-csv?plan_id=${planId}`, {
    method: 'POST',
    body: form,
    headers: authHeaders,
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

interface SyncJob {
  id: string;
  status: 'running' | 'success' | 'error';
  message: string;
}

export async function syncPlanData(planId: string, full = false, onProgress?: (message: string) => void) {
  let job: SyncJob;
  try {
    job = await request<SyncJob>(`/sync/plan/${planId}/jobs?full=${full}`, {
      method: 'POST', signal: AbortSignal.timeout(30_000),
    });
  } catch {
    throw new Error('Could not start or reconnect to sync. Please retry; any active sync will be reused.');
  }
  let failures = 0;
  const deadline = Date.now() + 65 * 60_000;
  while (job.status === 'running') {
    if (Date.now() > deadline) {
      throw new Error('Sync stopped reporting completion. Please retry to check its status.');
    }
    onProgress?.(job.message);
    await new Promise(resolve => setTimeout(resolve, 2000));
    try {
      job = await request<SyncJob>(`/sync/jobs/${job.id}`, { signal: AbortSignal.timeout(15_000) });
      failures = 0;
    } catch {
      failures += 1;
      if (failures >= 5) {
        throw new Error('Lost connection while checking sync. The server may still be syncing. Retry to reconnect to the active sync.');
      }
      onProgress?.('Reconnecting to sync…');
    }
  }
  if (job.status === 'error') throw new Error(job.message);
  return job;
}

export async function trainModels(planId: string) {
  const signal = AbortSignal.timeout(120_000);
  try {
    return await request<{ status: string }>(`/train?plan_id=${planId}`, { method: 'POST', signal });
  } catch (error) {
    if (signal.aborted || error instanceof TypeError) {
      throw new Error('Lost connection to the server during model training. It may still be running or may have restarted. Wait a moment before retrying.');
    }
    throw error;
  }
}

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
  edited_memo?: string;
  flag_color?: string;
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
  flag_color?: string;
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

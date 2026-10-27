/** API client — uses Vite proxy in dev, direct URL in production */

import { supabase } from '../lib/supabase';
import type { SplitLineData } from '../lib/splits';

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
  category_id?: string | null;
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

export type TransferState = 'none' | 'candidate' | 'ambiguous' | 'resolved' | 'linked';
export type WriteState = 'unsent' | 'created' | 'existing' | 'error' | 'unresolved';

export interface ImportRowData extends PredictionRow {
  id: string;
  status: string;
  edited_payee: string;
  edited_category: string;
  edited_category_id?: string | null;
  edited_memo: string;
  flag_color: string;
  amount_milliunits?: number | null;
  currency?: string;
  source_format?: string;
  source_status?: string;
  evidence?: Record<string, string>;
  account_id?: string | null;
  source_id?: string | null;
  import_id?: string | null;
  payee_confidence?: number;
  category_confidence?: number;
  payee_source?: string;
  category_source?: string;
  review_flag?: boolean;
  transfer_state?: TransferState;
  transfer_account_id?: string | null;
  reconciliation_state?: string;
  reconciliation_note?: string;
  write_state?: WriteState;
  write_error?: string;
  splits?: SplitLineData[];
  receipts?: ReceiptView[];
  ai?: AIView;
  alternatives?: Record<'payee' | 'category', Alternative[]>;
}

export type AIStatus = 'none' | 'suggested' | 'invalid' | 'refused' | 'error' | 'skipped_budget';

export interface AIView {
  status: AIStatus;
  payee: string | null;
  category_id: string | null;
  splits: SplitLineData[];
  explanation: string | null;
  uncertainty: 'low' | 'medium' | 'high' | null;
  evidence: { kind: string; ref: string }[];
  model: string | null;
  requested_at: string | null;
}

export interface Alternative {
  source: string;
  value: string | null;
  category_id: string | null;
  confidence: number;
  explanation: string;
}

export interface ReceiptItem { description: string; quantity: number; amount_milliunits: number }

export interface ReceiptView {
  receipt_id: string;
  state: 'proposed' | 'linked' | 'rejected';
  origin: 'auto' | 'user';
  role: 'primary' | 'supporting';
  score: number;
  signals: Record<string, number>;
  merchant: string;
  kind: 'purchase' | 'refund' | 'shipping' | 'payment';
  reference: string;
  purchase_date: string | null;
  total_milliunits: number | null;
  currency: string;
  status: string;
  items: ReceiptItem[];
  mailboxes: string[];
  split_suggestion: SplitLineData[];
}

export interface ImportReport {
  source_format: string;
  detected: number;
  eligible: number;
  pending: number;
  invalid: number;
  already_imported?: number;
  errors: { line: number; message: string }[];
}

export interface BatchContext {
  account_id: string | null;
  account_name: string | null;
  account_confirmed: boolean;
  source_format: string;
  report: Partial<ImportReport>;
}

export interface UploadResult extends Partial<BatchContext> {
  batch_id?: string;
  count: number;
  predictions: ImportRowData[];
}

export async function uploadCSV(planId: string, accountId: string, file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE}/upload-csv?plan_id=${planId}&account_id=${encodeURIComponent(accountId)}`, {
    method: 'POST',
    body: form,
    headers: authHeaders,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Upload error ${res.status}`);
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

export interface TargetScore { examples: number; correct: number; accuracy: number }

export interface ModelVersionInfo {
  id: string;
  status: 'candidate' | 'active' | 'retired' | 'rejected';
  note: string;
  created_at: string | null;
  activated_at: string | null;
  training: { training_rows?: number; payee_examples?: number; category_examples?: number };
  excluded: Record<string, number>;
  comparison: {
    holdout_from?: string | null;
    holdout_examples?: number;
    candidate?: Record<'payee' | 'category', TargetScore | null>;
    active?: Record<'payee' | 'category', TargetScore | null> | null;
  };
  files_present: boolean;
}

export interface TrainResult {
  status: 'promoted' | 'not_promoted';
  promoted: boolean;
  note: string;
  version: ModelVersionInfo | null;
}

export const fetchModels = () => request<{ versions: ModelVersionInfo[] }>('/models');

export const activateModel = (versionId: string) =>
  request<ModelVersionInfo>(`/models/${versionId}/activate`, { method: 'POST' });

export async function trainModels(planId: string) {
  const signal = AbortSignal.timeout(120_000);
  try {
    return await request<TrainResult>(`/train?plan_id=${planId}`, { method: 'POST', signal });
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
export interface ActiveBatchResponse extends BatchContext {
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
  edited_category_id?: string;
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

export function discardBatch(batchId: string) {
  return request<{ removed_rows: number; kept_rows: number }>(`/import-batches/${batchId}/discard`, { method: 'POST' });
}

export interface ReconcileResponse extends ActiveBatchResponse {
  warning: string | null;
  transfer_account_suggestions: string[];
  linked_count: number;
}

export const reconcileBatch = (batchId: string) =>
  request<ReconcileResponse>(`/import-batches/${batchId}/reconcile`, { method: 'POST' });

export type ResolveAction = 'link_existing' | 'confirm_new' | 'confirm_same';

export const resolveRow = (rowId: string, action: ResolveAction, ynabTransactionId?: string) =>
  request<ImportRowData>(`/import-rows/${rowId}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ action, ynab_transaction_id: ynabTransactionId }),
  });

export const decideReceipt = (rowId: string, receiptId: string, action: 'link' | 'unlink' | 'reject') =>
  request<ImportRowData>(`/import-rows/${rowId}/receipts`, {
    method: 'POST', body: JSON.stringify({ receipt_id: receiptId, action }),
  });

export const saveSplits = (rowId: string, lines: SplitLineData[]) =>
  request<ImportRowData>(`/import-rows/${rowId}/splits`, { method: 'PUT', body: JSON.stringify({ lines }) });

export const clearSplits = (rowId: string) =>
  request<ImportRowData>(`/import-rows/${rowId}/splits`, { method: 'DELETE' });

export interface LinkCandidate {
  ynab_transaction_id: string;
  date: string;
  amount_milliunits: number;
  payee: string;
  category: string;
  memo: string;
}

export const getCandidates = (rowId: string) =>
  request<{ candidates: LinkCandidate[] }>(`/import-rows/${rowId}/candidates`);

export const routeTransfer = (rowId: string, accountId: string | null) =>
  request<ImportRowData & { warning: string | null }>(`/import-rows/${rowId}/transfer`, {
    method: 'POST',
    body: JSON.stringify({ account_id: accountId }),
  });

export const reassignAccount = (batchId: string, accountId: string) =>
  request<ActiveBatchResponse>(`/import-batches/${batchId}/reassign-account`, {
    method: 'POST',
    body: JSON.stringify({ account_id: accountId }),
  });

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
/** Reference to a persisted import row; bank values (amount, date, account) are read server-side. */
export interface WriteBackRow {
  row_id: string;
  payee_name: string;
  category_name: string;
  category_id?: string;
  memo: string;
  flag_color?: string;
}

export type WriteStatus = 'created' | 'existing' | 'error' | 'unresolved' | 'dry_run_ok' | 'dry_run_error';

export interface WriteBackResultItem {
  index: number;
  row_id: string;
  source_id: string | null;
  import_id: string | null;
  date: string;
  payee_name: string;
  amount: number;
  amount_milliunits: number | null;
  status: WriteStatus;
  ynab_transaction_id: string | null;
  error: string | null;
}

export interface WriteBackResponse {
  mode: string;
  total: number;
  created: number;
  existing: number;
  errors: number;
  unresolved: number;
  results: WriteBackResultItem[];
}

export function writeBack(
  planId: string,
  mode: string,
  rows: WriteBackRow[],
  batchId?: string,
): Promise<WriteBackResponse> {
  return request<WriteBackResponse>('/write-back', {
    method: 'POST',
    body: JSON.stringify({ plan_id: planId, mode, rows, batch_id: batchId }),
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

/* ---- Optional features: receipts and AI ---- */
export interface FeatureState { enabled: boolean; reason: string; model?: string; monthly_budget_usd?: number }
export const fetchFeatures = () => request<{ receipts: FeatureState; ai: FeatureState }>('/features');

export interface MailboxFolderStatus { folder: string; last_fetch_at: string | null; last_uid: number; error: string }
export interface MailboxStatus { mailbox: string; complete: boolean; folders: MailboxFolderStatus[]; messages_seen: number }
export interface ReceiptStatus {
  feature: FeatureState;
  mailboxes: MailboxStatus[];
  receipts: Record<string, number>;
  running: boolean;
}
export interface ReceiptFetchResult {
  messages: number;
  new_receipts: number;
  duplicates: number;
  not_receipts: number;
  ai_note: string;
  folders: { mailbox: string; folder: string; error: string }[];
}

export const fetchReceiptStatus = () => request<ReceiptStatus>('/receipts/status');
export const runReceiptFetch = () =>
  request<ReceiptFetchResult>('/receipts/fetch', { method: 'POST', signal: AbortSignal.timeout(15 * 60_000) });

export interface AIUsageSummary {
  month_start: string;
  spent_usd: number;
  budget_usd: number;
  remaining_usd: number;
  requests: Record<string, number>;
  estimate_per_request_usd: number;
  model: string;
  feature: FeatureState;
}

export const fetchAIUsage = () => request<AIUsageSummary>('/ai/usage');

export interface AISuggestResult extends ActiveBatchResponse {
  requested: number;
  suggested?: number;
  invalid?: number;
  refused?: number;
  error?: number;
  skipped_budget?: number;
  usage: Omit<AIUsageSummary, 'feature'>;
}

export const aiSuggestBatch = (batchId: string, rowIds?: string[]) =>
  request<AISuggestResult>(`/import-batches/${batchId}/ai-suggest`, {
    method: 'POST', body: JSON.stringify({ row_ids: rowIds ?? null }), signal: AbortSignal.timeout(15 * 60_000),
  });

/* ---- Health ---- */
export const healthCheck = () => request<{ status: string }>('/health');

/* ---- Receipts page: review, corrections and spending ---- */
export type ReceiptKind = 'purchase' | 'refund' | 'shipping' | 'payment';
export interface ReceiptItem {
  index: number;
  description: string;
  quantity: number;
  amount_milliunits: number;
  category_id: string | null;
  category_name: string | null;
  category_source: 'user' | 'split' | 'row' | 'ai' | 'uncategorized';
  note?: string;
  category_note?: string;
}
export interface ReceiptSummary {
  id: string;
  status: string;
  kind: ReceiptKind;
  merchant: string;
  date: string | null;
  purchase_date: string | null;
  total_milliunits: number | null;
  currency: string;
  subject: string;
  mailboxes: string[];
  linked_rows: number;
  excluded: boolean;
  user_fields: string[];
  error: string;
  possible_duplicates: string[];
}
export interface ReceiptRowSummary {
  id: string;
  date: string;
  amount_milliunits: number | null;
  currency: string;
  bank_text: string;
  write_state: string;
  in_ynab: boolean;
  batch: string | null;
  category: string | null;
  split: boolean;
}
export interface ReceiptMatchView {
  state: 'proposed' | 'linked' | 'rejected' | null;
  origin?: string;
  role?: string;
  score: number;
  signals: Record<string, number>;
  row: ReceiptRowSummary;
}
export interface ReceiptDetail extends ReceiptSummary {
  reference: string;
  card_hint: string;
  sender: string;
  message_date: string | null;
  extraction_method: string;
  local_total_milliunits: number | null;
  extracted: Record<string, unknown>;
  items: ReceiptItem[];
  sources: { mailbox: string; folder: string }[];
  text_excerpt: string;
  matches: ReceiptMatchView[];
  note?: string;
}
export interface ReceiptPage { total: number; page: number; page_size: number; receipts: ReceiptSummary[] }
export interface ReceiptFilters {
  status?: string; kind?: string; linked?: string; mailbox?: string; q?: string; from?: string; to?: string;
  page?: number; page_size?: number;
}
export interface AIEstimate { requests: number; estimate_usd: number; spent_usd: number; budget_usd: number; remaining_usd: number }
export interface ItemCorrection { description: string; quantity: number; amount_milliunits: number; category_id?: string | null; category_source?: string }

function query(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : '';
}

export const listReceipts = (filters: ReceiptFilters) =>
  request<ReceiptPage>(`/receipts${query({ ...filters })}`);
export const fetchReceipt = (id: string, planId?: string) =>
  request<ReceiptDetail>(`/receipts/${id}${query({ plan_id: planId })}`);
export const correctReceipt = (id: string, body: { fields?: Record<string, unknown>; reset?: string[]; excluded?: boolean }, planId?: string) =>
  request<ReceiptDetail>(`/receipts/${id}${query({ plan_id: planId })}`, { method: 'PATCH', body: JSON.stringify(body) });
export const classifyReceipt = (id: string, as: 'receipt' | 'not_receipt', planId?: string) =>
  request<ReceiptDetail>(`/receipts/${id}/classify${query({ plan_id: planId })}`, {
    method: 'POST', body: JSON.stringify({ as }), signal: AbortSignal.timeout(5 * 60_000),
  });
export const estimateReread = (id: string) =>
  request<AIEstimate>(`/receipts/${id}/reread`, { method: 'POST', body: JSON.stringify({ estimate_only: true }) });
export const rereadReceipt = (id: string, planId?: string) =>
  request<ReceiptDetail>(`/receipts/${id}/reread${query({ plan_id: planId })}`, {
    method: 'POST', body: JSON.stringify({}), signal: AbortSignal.timeout(5 * 60_000),
  });
export const receiptCandidates = (id: string, planId?: string) =>
  request<{ candidates: ReceiptMatchView[] }>(`/receipts/${id}/candidates${query({ plan_id: planId })}`);
export const linkReceiptRow = (id: string, rowId: string, action: 'link' | 'unlink' | 'reject', planId?: string) =>
  request<ReceiptDetail>(`/receipts/${id}/links${query({ plan_id: planId })}`, {
    method: 'POST', body: JSON.stringify({ row_id: rowId, action }),
  });
export const setItemCategory = (id: string, index: number, categoryId: string | null, planId?: string) =>
  request<ReceiptDetail>(`/receipts/${id}/items/${index}/category${query({ plan_id: planId })}`, {
    method: 'PUT', body: JSON.stringify({ category_id: categoryId }),
  });

export interface SpendingLine {
  receipt_id: string;
  merchant: string;
  date: string | null;
  description: string;
  amount_milliunits: number;
  item_index: number | null;
  category_id: string | null;
  category_name: string | null;
  category_source: string;
}
export interface SpendingGroup { key: string; label: string; total_milliunits: number; months: Record<string, number>; lines: SpendingLine[] }
export interface SpendingReport {
  currency: string;
  group: 'category' | 'merchant' | 'item';
  months: string[];
  total_milliunits: number;
  uncategorized_milliunits: number;
  uncategorized_share: number;
  receipts_counted: number;
  uncounted: { receipt_id: string; merchant: string; date: string | null; total_milliunits: number | null; currency: string; reason: string }[];
  uncategorized_receipt_ids: string[];
  groups: SpendingGroup[];
}
export const fetchSpending = (params: { plan_id?: string; from?: string; to?: string; group: string; q?: string }) =>
  request<SpendingReport>(`/spending${query(params)}`);
export interface CategorizeItemsResult { receipts: number; items_filled: number; failed: number; budget_reached: boolean; errors: string[] }
export const estimateCategorizeItems = (receiptIds: string[], planId?: string) =>
  request<AIEstimate>('/receipts/categorize-items', {
    method: 'POST', body: JSON.stringify({ receipt_ids: receiptIds, plan_id: planId ?? null, estimate_only: true }),
  });
export const categorizeItems = (receiptIds: string[], planId?: string) =>
  request<CategorizeItemsResult>('/receipts/categorize-items', {
    method: 'POST', body: JSON.stringify({ receipt_ids: receiptIds, plan_id: planId ?? null }),
    signal: AbortSignal.timeout(15 * 60_000),
  });

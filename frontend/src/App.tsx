import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  Workflow, RefreshCw, Loader2, ArrowLeft, Download, Upload as UploadIcon,
  CheckCircle, AlertCircle, XCircle, Settings, BarChart3, LogOut, Trash2, Receipt,
} from 'lucide-react';
import { fetchPlans, uploadCSV, syncBudgets, syncPlanData, fetchAccounts, fetchCategories, fetchPayees, writeBack, createRule, reclassify, fetchActiveBatch, updateImportRows, completeBatch, discardBatch, reconcileBatch, resolveRow, routeTransfer, reassignAccount } from './api/client';
import type { Plan, ImportRowData, Account, CategoryGroup, PayeeItem, WriteBackResponse, BatchContext, ResolveAction } from './api/client';
import { handledRowIds, removeHandled, canCompleteBatch, describeImport, SOURCE_LABELS } from './lib/importFlow';
import ResolutionPanel from './components/ResolutionPanel';
import FileDrop from './components/FileDrop';
import StatsBar from './components/StatsBar';
import ReviewTable, { type ReviewedRow, type ReviewStatus } from './components/ReviewTable';
import { toReviewedRows } from './lib/reviewRows';
import AskAIButton from './components/AskAIButton';
import SettingsPage from './components/SettingsPage';
import MetricsDashboard from './components/MetricsDashboard';
import ReceiptsPage from './components/ReceiptsPage';
import LoginPage from './components/LoginPage';
import { supabase } from './lib/supabase';
import type { Session } from '@supabase/supabase-js';

const queryClient = new QueryClient();

type View = 'import' | 'review' | 'push' | 'settings' | 'dashboard' | 'receipts';

function AppContent({ onLogout }: { onLogout?: () => void }) {
  const [selectedPlanId, setSelectedPlanId] = useState<string>('');
  const [batchId, setBatchId] = useState<string | null>(null);
  const [predictions, setPredictions] = useState<ImportRowData[]>([]);
  const [reviewRows, setReviewRows] = useState<ReviewedRow[]>([]);
  const [view, setView] = useState<View>('import');
  const [openReceiptId, setOpenReceiptId] = useState<string | null>(null);
  const showReceipt = useCallback((id: string) => {
    setOpenReceiptId(id);
    setView('receipts');
  }, []);
  const [selectedAccountId, setSelectedAccountId] = useState<string>('');
  const [writeMode, setWriteMode] = useState<string>('dry_run');
  const [writeResult, setWriteResult] = useState<WriteBackResponse | null>(null);
  const [ruleToast, setRuleToast] = useState<string | null>(null);
  const [isReclassifying, setIsReclassifying] = useState(false);
  const [changedRows, setChangedRows] = useState<Set<number>>(new Set());
  const [batchContext, setBatchContext] = useState<BatchContext | null>(null);
  const [suggestedAccountIds, setSuggestedAccountIds] = useState<string[]>([]);
  const [resolveBusy, setResolveBusy] = useState(false);
  const [resolveWarning, setResolveWarning] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [discardError, setDiscardError] = useState<string | null>(null);

  const applyBatchContext = (ctx: Partial<BatchContext>) => {
    setBatchContext({
      account_id: ctx.account_id ?? null,
      account_name: ctx.account_name ?? null,
      account_confirmed: ctx.account_confirmed ?? false,
      source_format: ctx.source_format ?? '',
      report: ctx.report ?? {},
    });
    if (ctx.account_id) setSelectedAccountId(ctx.account_id);
  };

  const plansQuery = useQuery({ queryKey: ['plans'], queryFn: fetchPlans });

  const accountsQuery = useQuery({
    queryKey: ['accounts', selectedPlanId],
    queryFn: () => fetchAccounts(selectedPlanId),
    enabled: !!selectedPlanId,
  });

  const categoriesQuery = useQuery({
    queryKey: ['categories', selectedPlanId],
    queryFn: () => fetchCategories(selectedPlanId),
    enabled: !!selectedPlanId,
  });

  const payeesQuery = useQuery({
    queryKey: ['payees', selectedPlanId],
    queryFn: () => fetchPayees(selectedPlanId),
    enabled: !!selectedPlanId,
  });

  // Load active import batch from DB on startup
  const activeBatchQuery = useQuery({
    queryKey: ['activeBatch', selectedPlanId],
    queryFn: () => fetchActiveBatch(selectedPlanId),
    enabled: !!selectedPlanId,
    retry: false,
  });

  // Populate state from active batch
  useEffect(() => {
    if (activeBatchQuery.data && !batchId) {
      const { batch_id, rows } = activeBatchQuery.data;
      applyBatchContext(activeBatchQuery.data);
      setBatchId(batch_id);
      setPredictions(rows);
      setReviewRows(toReviewedRows(rows));
    }
  }, [activeBatchQuery.data, batchId]);

  const handleDiscard = async () => {
    if (!batchId) return;
    try {
      await discardBatch(batchId);
      setBatchId(null);
      setBatchContext(null);
      setPredictions([]);
      setReviewRows([]);
      setWriteResult(null);
      setResolveWarning(null);
      setView('import');
      queryClient.removeQueries({ queryKey: ['activeBatch', selectedPlanId] });
    } catch (e) {
      setDiscardError((e as Error).message);
    } finally {
      setConfirmDiscard(false);
    }
  };

  const discardControl = batchId ? (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      {confirmDiscard ? (
        <>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Remove this import? Rows already in YNAB stay.</span>
          <button className="btn btn-danger" onClick={handleDiscard}>Remove</button>
          <button className="btn btn-ghost" onClick={() => setConfirmDiscard(false)}>Cancel</button>
        </>
      ) : (
        <button className="btn btn-ghost" onClick={() => { setDiscardError(null); setConfirmDiscard(true); }}>
          <Trash2 size={14} /> Remove import
        </button>
      )}
      {discardError && <span style={{ color: 'var(--danger)', fontSize: '0.85rem' }}>{discardError}</span>}
    </span>
  ) : null;

  const syncMutation = useMutation({
    mutationFn: async () => {
      await syncBudgets();
      // After budgets are synced, if we have a selected plan, sync its full data
      if (selectedPlanId) {
        await syncPlanData(selectedPlanId);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans'] });
      queryClient.invalidateQueries({ queryKey: ['metrics', selectedPlanId] });
      queryClient.invalidateQueries({ queryKey: ['accounts', selectedPlanId] });
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => {
      if (!selectedPlanId) throw new Error('Select a budget first');
      if (!selectedAccountId) throw new Error('Select the target account for this file first');
      return uploadCSV(selectedPlanId, selectedAccountId, file);
    },
    onSuccess: (data) => {
      applyBatchContext(data);
      setResolveWarning(null);
      setBatchId(data.batch_id || null);
      setPredictions(data.predictions);
      setReviewRows(toReviewedRows(data.predictions));
      setView('review');
    },
  });

  const writeMutation = useMutation({
    mutationFn: () => {
      // Rows are referenced by id; amount, date and account come from the server's persisted evidence
      const accepted = reviewRows.filter((r) => r.status === 'accepted');
      const rows = accepted.map((r) => ({
        row_id: r.id,
        payee_name: r.editedPayee,
        category_name: r.editedCategory,
        category_id: r.editedCategoryId || undefined,
        memo: r.editedMemo || r.original_memo,
        flag_color: r.flagColor || undefined,
      }));
      return writeBack(selectedPlanId, writeMode, rows, batchId ?? undefined);
    },
    onSuccess: async (data) => {
      setWriteResult(data);
      // Dry runs change nothing; real writes drop only rows YNAB confirmed (created or already existing)
      if (data.mode === 'dry_run' || !batchId) return;
      const handled = handledRowIds(data);
      const failures = new Map(data.results.filter((r) => r.status === 'error' || r.status === 'unresolved').map((r) => [r.row_id, r]));
      const remainingRows = removeHandled(reviewRows, handled).map((r) => {
        const f = failures.get(r.id);
        return f ? { ...r, write_state: f.status as 'error' | 'unresolved', write_error: f.error ?? '' } : r;
      });
      setPredictions(removeHandled(predictions, handled));
      setReviewRows(remainingRows);
      if (handled.size > 0 && canCompleteBatch(remainingRows)) {
        try {
          await completeBatch(batchId);
          setBatchId(null);
          setBatchContext(null);
          setPredictions([]);
          setReviewRows([]);
          queryClient.invalidateQueries({ queryKey: ['activeBatch', selectedPlanId] });
        } catch {
          // The server still sees retryable rows; keep the batch open
        }
      }
    },
  });

  const plans: Plan[] = plansQuery.data || [];
  const accounts: Account[] = (accountsQuery.data || []).filter((a) => !a.closed);
  const categoryGroups: CategoryGroup[] = categoriesQuery.data || [];
  const payees: PayeeItem[] = payeesQuery.data || [];

  if (plans.length > 0 && !selectedPlanId) {
    setSelectedPlanId(plans[0].id);
  }

  /* ---- Review callbacks ---- */
  const handleUpdateRow = useCallback((index: number, update: Partial<ReviewedRow>) => {
    setReviewRows((prev) => {
      const next = [...prev];
      const row = { ...next[index], ...update };
      next[index] = row;
      // Persist to DB
      updateImportRows([{
        id: row.id,
        status: update.status ?? row.status,
        edited_payee: update.editedPayee ?? row.editedPayee,
        edited_category: update.editedCategory ?? row.editedCategory,
        edited_category_id: row.editedCategoryId || undefined,
        edited_memo: update.editedMemo ?? row.editedMemo ?? '',
        flag_color: update.flagColor ?? row.flagColor ?? '',
      }]).catch(() => {});
      return next;
    });
  }, []);

  const handleBulkAction = useCallback((indices: number[], status: ReviewStatus) => {
    setReviewRows((prev) => {
      const next = [...prev];
      const updates = indices.map((i) => {
        next[i] = { ...next[i], status };
        return { id: next[i].id, status };
      });
      // Persist to DB
      updateImportRows(updates).catch(() => {});
      return next;
    });
  }, []);

  /* ---- Re-classify current rows through the pipeline ---- */
  const reclassifyRows = useCallback(async () => {
    if (!selectedPlanId || !batchId) return;
    setIsReclassifying(true);
    try {
      const result = await reclassify(selectedPlanId, batchId);
      setPredictions(result.predictions);
      setReviewRows((prev) => {
        const updated = toReviewedRows(result.predictions);
        const changed = new Set<number>();
        updated.forEach((newRow, i) => {
          const old = prev[i];
          if (!old) return;
          if (old.payee !== newRow.payee || old.category !== newRow.category || old.source !== newRow.source) {
            changed.add(newRow.row_index);
          }
        });
        if (changed.size > 0) {
          setChangedRows(changed);
          setTimeout(() => setChangedRows(new Set()), 3000);
        }
        return updated;
      });
      const count = result.predictions.length;
      setRuleToast(`Reclassification complete — ${count} transactions re-evaluated`);
      setTimeout(() => setRuleToast(null), 2000);
    } finally {
      setIsReclassifying(false);
    }
  }, [selectedPlanId, batchId]);

  /* ---- Create rule from correction (FR-12) ---- */
  const handleCreateRule = useCallback(async (row: ReviewedRow) => {
    if (!selectedPlanId || !row.editedPayee) return;
    try {
      await createRule({
        plan_id: selectedPlanId,
        name: `${row.merchant_stem || row.cleaned_memo} → ${row.editedPayee}`,
        match_type: 'contains',
        pattern: row.merchant_stem || row.cleaned_memo,
        assign_payee: row.editedPayee,
        assign_category: row.editedCategory || undefined,
      });
      queryClient.invalidateQueries({ queryKey: ['rules', selectedPlanId] });
      // Re-classify all rows so the new rule takes effect immediately
      await reclassifyRows();
      setRuleToast(`Rule created & applied: "${row.merchant_stem}" → ${row.editedPayee}`);
      setTimeout(() => setRuleToast(null), 3000);
    } catch (e) {
      setRuleToast(`Error: ${(e as Error).message}`);
      setTimeout(() => setRuleToast(null), 3000);
    }
  }, [selectedPlanId, reclassifyRows]);

  /* ---- Reload rows after server-side changes (e.g. receipts matched by a fetch) ---- */
  const refreshActiveRows = useCallback(async () => {
    if (!selectedPlanId || !batchId) return;
    try {
      const active = await fetchActiveBatch(selectedPlanId);
      if (active.batch_id === batchId) {
        setPredictions(active.rows);
        setReviewRows(toReviewedRows(active.rows));
      }
    } catch {
      // no active batch any more: nothing to refresh
    }
  }, [selectedPlanId, batchId]);

  /* ---- Reconciliation / repayment resolution ---- */
  const replaceRows = (rows: ImportRowData[]) => {
    setPredictions(rows);
    setReviewRows(toReviewedRows(rows));
  };

  const mergeUpdatedRow = (updated: Partial<ImportRowData> & { id: string }) => {
    if (updated.write_state === 'created' || updated.write_state === 'existing') {
      setPredictions((prev) => prev.filter((p) => p.id !== updated.id));
      setReviewRows((prev) => prev.filter((r) => r.id !== updated.id));
      return;
    }
    setPredictions((prev) => prev.map((p) => (p.id === updated.id ? { ...p, ...updated } as ImportRowData : p)));
    setReviewRows((prev) => prev.map((r) => (r.id === updated.id ? { ...r, ...updated } as ReviewedRow : r)));
  };

  const runResolution = async (task: () => Promise<void>) => {
    setResolveBusy(true);
    try {
      await task();
    } catch (e) {
      setResolveWarning((e as Error).message);
    } finally {
      setResolveBusy(false);
    }
  };

  const handleRecheck = useCallback(() => runResolution(async () => {
    if (!batchId) return;
    const res = await reconcileBatch(batchId);
    replaceRows(res.rows);
    setSuggestedAccountIds(res.transfer_account_suggestions);
    setResolveWarning(res.warning);
    if (res.linked_count > 0) {
      setRuleToast(`${res.linked_count} row(s) already exist in YNAB and were removed from the queue`);
      setTimeout(() => setRuleToast(null), 3000);
    }
  }), [batchId]);

  const handleResolve = (rowId: string, action: ResolveAction, ynabId?: string) => runResolution(async () => {
    mergeUpdatedRow(await resolveRow(rowId, action, ynabId));
    setResolveWarning(null);
  });

  const handleRoute = (rowId: string, accountId: string | null) => runResolution(async () => {
    const { warning, ...row } = await routeTransfer(rowId, accountId);
    mergeUpdatedRow(row);
    setResolveWarning(warning);
  });

  const handleConfirmAccount = (accountId: string) => runResolution(async () => {
    if (!batchId) return;
    const res = await reassignAccount(batchId, accountId);
    applyBatchContext(res);
    replaceRows(res.rows);
  });

  /* ---- Export CSV ---- */
  const handleExport = () => {
    const accepted = reviewRows.filter((r) => r.status === 'accepted');
    const header = 'Date,Payee,Category,Memo,Inflow,Outflow';
    const lines = accepted.map((r) => {
      const memo = `"${r.original_memo.replace(/"/g, '""')}"`;
      const inflow = r.amount >= 0 ? r.amount.toFixed(2) : '';
      const outflow = r.amount < 0 ? Math.abs(r.amount).toFixed(2) : '';
      return `${r.date},"${r.editedPayee}","${r.editedCategory}",${memo},${inflow},${outflow}`;
    });
    const csv = [header, ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ynab-flow-export.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const acceptedCount = reviewRows.filter((r) => r.status === 'accepted').length;

  const goBack = () => {
    if (view === 'push') {
      setWriteResult(null);
      // If no rows remain after push, go back to import; otherwise review
      setView(reviewRows.length > 0 ? 'review' : 'import');
    }
    else if (view === 'review') setView('import');
    else if (view === 'settings') setView('import');
    else if (view === 'dashboard') setView('import');
    else if (view === 'receipts') setView(reviewRows.length > 0 ? 'review' : 'import');
  };

  return (
    <div className="app-layout">
      {/* ---- Header ---- */}
      <header className="app-header">
        <div className="app-header-title">
          {view !== 'import' && (
            <button className="btn btn-ghost" style={{ padding: '4px 8px', marginRight: 4 }} onClick={goBack}>
              <ArrowLeft size={18} />
            </button>
          )}
          <Workflow className="logo" size={28} />
          <span>YNAB Flow</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {plans.length > 0 && (
            <div className="select-wrapper">
              <select value={selectedPlanId} onChange={(e) => setSelectedPlanId(e.target.value)}>
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}
          {view === 'review' && (
            <>
              <button className="btn btn-secondary" onClick={handleExport}>
                <Download size={16} /> Export CSV
              </button>
              <button
                className="btn btn-primary"
                onClick={() => setView('push')}
                disabled={acceptedCount === 0}
              >
                <UploadIcon size={16} /> Push to YNAB ({acceptedCount})
              </button>
            </>
          )}
          <button
            className="btn btn-secondary"
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending}
            title="Fetch what changed in YNAB since the last sync: budgets, accounts, categories, payees and transactions"
          >
            {syncMutation.isPending ? (
              <Loader2 size={16} className="loading-pulse" />
            ) : (
              <RefreshCw size={16} />
            )}
            Sync
          </button>
          <button
            className={`btn ${view === 'receipts' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => { setOpenReceiptId(null); setView(view === 'receipts' ? 'import' : 'receipts'); }}
            title="Receipts and spending"
          >
            <Receipt size={18} />
          </button>
          <button
            className={`btn ${view === 'dashboard' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setView(view === 'dashboard' ? 'import' : 'dashboard')}
            title="Dashboard"
          >
            <BarChart3 size={18} />
          </button>
          <button
            className={`btn ${view === 'settings' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setView(view === 'settings' ? 'import' : 'settings')}
            title="Settings"
          >
            <Settings size={18} />
          </button>
          {onLogout && (
            <button className="btn btn-ghost" onClick={onLogout} title="Sign out">
              <LogOut size={18} />
            </button>
          )}
        </div>
      </header>

      {/* ---- Toast ---- */}
      {(isReclassifying || ruleToast) && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 1000,
          background: 'var(--bg-card)', border: '1px solid var(--accent)',
          borderRadius: 'var(--radius-md)', padding: '12px 20px',
          boxShadow: 'var(--shadow-glow)', color: 'var(--text-primary)',
          fontSize: '0.875rem', animation: 'fadeInUp 0.3s ease',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          {isReclassifying ? (
            <><Loader2 size={16} className="loading-pulse" /> Reclassifying transactions…</>
          ) : (
            <><CheckCircle size={16} style={{ color: 'var(--success)' }} /> {ruleToast}</>
          )}
        </div>
      )}

      {/* ---- Main ---- */}
      <main className={`app-main${view === 'review' ? ' full-width' : ''}`}>
        {/* IMPORT */}
        {view === 'import' && (
          <>
            <section style={{ marginBottom: 32 }} className="animate-in">
              <h2 style={{ marginBottom: 16 }}>Import Transactions</h2>
              <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
                <label htmlFor="import-account" style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                  Target account for this file
                </label>
                <div className="select-wrapper">
                  <select
                    id="import-account"
                    value={selectedAccountId}
                    onChange={(e) => setSelectedAccountId(e.target.value)}
                  >
                    <option value="">Select account…</option>
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>{a.name} ({a.type})</option>
                    ))}
                  </select>
                </div>
              </div>
              <FileDrop
                onFile={(file) => uploadMutation.mutate(file)}
                disabled={uploadMutation.isPending || !selectedPlanId || !selectedAccountId}
              />
              {!selectedAccountId && (
                <p style={{ textAlign: 'center', marginTop: 8, color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                  Choose the account first: predictions and duplicate checks depend on it.
                </p>
              )}
              {uploadMutation.isPending && (
                <p style={{ textAlign: 'center', marginTop: 16, color: 'var(--text-muted)' }} className="loading-pulse">
                  <Loader2 size={20} style={{ verticalAlign: 'middle', marginRight: 8 }} />
                  Processing transactions…
                </p>
              )}
              {uploadMutation.isError && (
                <p style={{ textAlign: 'center', marginTop: 16, color: 'var(--danger)' }}>
                  Error: {(uploadMutation.error as Error).message}
                </p>
              )}
            </section>
            {predictions.length > 0 && (
              <section className="animate-in" style={{ animationDelay: '100ms' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div>
                    <h2>Last Import ({predictions.length} transactions)</h2>
                    {batchContext && (
                      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: 4 }}>
                        {SOURCE_LABELS[batchContext.source_format] || batchContext.source_format || 'Earlier import'}
                        {' → '}{batchContext.account_name || 'account not confirmed'}
                        {describeImport(batchContext.report) && ` · ${describeImport(batchContext.report)}`}
                      </p>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    {discardControl}
                    <button className="btn btn-primary" onClick={() => setView('review')}>
                      Review & Approve →
                    </button>
                  </div>
                </div>
                <StatsBar predictions={predictions} />
              </section>
            )}
            {predictions.length === 0 && !uploadMutation.isPending && (
              <section className="animate-in" style={{ textAlign: 'center', padding: '64px 0', color: 'var(--text-muted)' }}>
                <Workflow size={64} style={{ color: 'var(--accent-subtle)', marginBottom: 16 }} />
                <p style={{ fontSize: '1rem' }}>Upload a CSV file to classify your transactions</p>
                <p style={{ fontSize: '0.85rem', marginTop: 4 }}>Supports Swisscard, PostFinance / BCGE and generic Date/Memo/Inflow/Outflow exports</p>
              </section>
            )}
          </>
        )}

        {/* REVIEW */}
        {view === 'review' && (
          <section className="animate-in">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <h2>Review Transactions</h2>
              <span style={{ display: 'inline-flex', gap: 12, alignItems: 'center' }}>
                <AskAIButton batchId={batchId} rows={reviewRows} onDone={(res) => replaceRows(res.rows)} />
                {discardControl}
              </span>
            </div>
            {batchContext && (
              <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: 12 }}>
                {SOURCE_LABELS[batchContext.source_format] || batchContext.source_format || 'Earlier import'}
                {' → '}<strong>{batchContext.account_name || 'account not confirmed'}</strong>
                {describeImport(batchContext.report) && ` · ${describeImport(batchContext.report)}`}
              </p>
            )}
            {batchContext && !batchContext.account_confirmed && (
              <div className="card" style={{ padding: 16, marginBottom: 16, borderColor: 'var(--warning)' }}>
                <strong>This batch predates account selection.</strong>
                <p style={{ fontSize: '0.85rem', margin: '4px 0 8px' }}>Confirm the account it belongs to before pushing; predictions and duplicate checks will be redone.</p>
                <div className="select-wrapper">
                  <select value="" onChange={(e) => e.target.value && handleConfirmAccount(e.target.value)} disabled={resolveBusy}>
                    <option value="">Confirm account…</option>
                    {accounts.map((a) => (<option key={a.id} value={a.id}>{a.name} ({a.type})</option>))}
                  </select>
                </div>
              </div>
            )}
            {batchContext?.report?.invalid ? (
              <details className="card" style={{ padding: 12, marginBottom: 16 }}>
                <summary>{batchContext.report.invalid} row(s) in the file could not be read and were skipped</summary>
                <ul style={{ fontSize: '0.8rem', marginTop: 8 }}>
                  {(batchContext.report.errors ?? []).map((e) => (<li key={e.line}>Line {e.line}: {e.message}</li>))}
                </ul>
              </details>
            ) : null}
            <ResolutionPanel
              rows={reviewRows}
              accounts={accounts}
              cardAccountId={batchContext?.account_id ?? null}
              suggestedAccountIds={suggestedAccountIds}
              busy={resolveBusy}
              warning={resolveWarning}
              onRecheck={handleRecheck}
              onResolve={handleResolve}
              onRoute={handleRoute}
            />
            <StatsBar predictions={predictions} reviewRows={reviewRows} />
            <ReviewTable
              rows={reviewRows}
              categoryGroups={categoryGroups}
              payees={payees}
              onUpdateRow={handleUpdateRow}
              onRowReplaced={mergeUpdatedRow}
              onOpenReceipt={showReceipt}
              onBulkAction={handleBulkAction}
              onCreateRule={handleCreateRule}
              changedRows={changedRows}
            />
          </section>
        )}

        {/* PUSH */}
        {view === 'push' && (
          <section className="animate-in">
            <h2 style={{ marginBottom: 24 }}>Push to YNAB</h2>
            <div className="card" style={{ padding: 32, maxWidth: 600 }}>
              <h3 style={{ marginBottom: 20 }}>Write-Back Configuration</h3>
              <div style={{ marginBottom: 20 }}>
                <label style={{ display: 'block', marginBottom: 6, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Target Account</label>
                <div style={{ padding: '8px 12px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)' }}>
                  <strong>{batchContext?.account_name || 'Not confirmed'}</strong>
                  <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontSize: '0.8rem' }}>
                    from {SOURCE_LABELS[batchContext?.source_format ?? ''] || batchContext?.source_format || 'import'} · set at import
                  </span>
                </div>
              </div>
              <div style={{ marginBottom: 20 }}>
                <label style={{ display: 'block', marginBottom: 6, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Write Mode</label>
                <div className="select-wrapper">
                  <select value={writeMode} onChange={(e) => setWriteMode(e.target.value)}>
                    <option value="dry_run">🔍 Dry Run — validate only</option>
                    <option value="create">✏️ Create — create all</option>
                    <option value="create_or_skip">🛡️ Create or Skip — skip duplicates</option>
                  </select>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginTop: 6 }}>
                  Transactions are created unapproved in YNAB. Accepting a row here is local review only.
                </p>
              </div>
              <div style={{
                padding: 16, borderRadius: 'var(--radius-md)', background: 'var(--bg-surface)',
                marginBottom: 24, fontSize: '0.9rem',
              }}>
                <strong style={{ color: 'var(--text-primary)' }}>{acceptedCount}</strong>
                <span style={{ color: 'var(--text-secondary)' }}> accepted transactions → </span>
                <strong style={{ color: 'var(--text-primary)' }}>
                  {batchContext?.account_name || '—'}
                </strong>
              </div>
              <button
                className="btn btn-primary"
                style={{ width: '100%', justifyContent: 'center', padding: '14px 24px' }}
                onClick={() => writeMutation.mutate()}
                disabled={writeMutation.isPending || !batchContext?.account_confirmed || !batchContext?.account_id}
              >
                {writeMutation.isPending ? (
                  <><Loader2 size={18} className="loading-pulse" /> Processing…</>
                ) : (
                  <><UploadIcon size={18} /> {writeMode === 'dry_run' ? 'Run Dry Validation' : 'Push to YNAB'}</>
                )}
              </button>
              {writeMutation.isError && (
                <p style={{ marginTop: 16, color: 'var(--danger)', textAlign: 'center' }}>
                  Error: {(writeMutation.error as Error).message}
                </p>
              )}
            </div>

            {writeResult && (
              <div className="card animate-in" style={{ padding: 32, marginTop: 24 }}>
                <h3 style={{ marginBottom: 20 }}>
                  {writeResult.mode === 'dry_run' ? '🔍 Dry Run Results' : '✅ Write-Back Results'}
                </h3>
                <div className="stats-row">
                  <div className="card stat-card">
                    <div className="stat-value">{writeResult.total}</div>
                    <div className="stat-label">Total</div>
                  </div>
                  <div className="card stat-card">
                    <div className="stat-value" style={{ color: 'var(--success)' }}>
                      <CheckCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                      {writeResult.mode === 'dry_run' ? writeResult.results.filter((r) => r.status === 'dry_run_ok').length : writeResult.created}
                    </div>
                    <div className="stat-label">{writeResult.mode === 'dry_run' ? 'Valid' : 'Created'}</div>
                  </div>
                  {writeResult.existing > 0 && (
                    <div className="card stat-card">
                      <div className="stat-value" style={{ color: 'var(--warning)' }}>
                        <AlertCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                        {writeResult.existing}
                      </div>
                      <div className="stat-label">Already in YNAB</div>
                    </div>
                  )}
                  {writeResult.unresolved > 0 && (
                    <div className="card stat-card">
                      <div className="stat-value" style={{ color: 'var(--warning)' }}>
                        <AlertCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                        {writeResult.unresolved}
                      </div>
                      <div className="stat-label">Unresolved</div>
                    </div>
                  )}
                  {writeResult.errors > 0 && (
                    <div className="card stat-card">
                      <div className="stat-value" style={{ color: 'var(--danger)' }}>
                        <XCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                        {writeResult.errors}
                      </div>
                      <div className="stat-label">Errors</div>
                    </div>
                  )}
                </div>
                {writeResult.results.length > 0 && (
                  <div style={{ overflow: 'auto', maxHeight: '40vh', marginTop: 16 }}>
                    <table className="results-table">
                      <thead>
                        <tr><th>#</th><th>Date</th><th>Payee</th><th>Amount</th><th>Status</th><th>Details</th></tr>
                      </thead>
                      <tbody>
                        {writeResult.results.map((r) => (
                          <tr key={r.row_id}>
                            <td>{r.index + 1}</td>
                            <td>{r.date}</td>
                            <td>{r.payee_name}</td>
                            <td className={`amount ${r.amount >= 0 ? 'positive' : 'negative'}`}>
                              {r.amount >= 0 ? '+' : ''}{r.amount.toFixed(2)}
                            </td>
                            <td>
                              <span className={`badge ${
                                r.status === 'created' || r.status === 'dry_run_ok' ? 'badge-success' :
                                r.status === 'existing' || r.status === 'unresolved' ? 'badge-warning' : 'badge-danger'
                              }`}>
                                {r.status.replace(/_/g, ' ')}
                              </span>
                            </td>
                            <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                              {r.ynab_transaction_id || r.error || '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {/* SETTINGS */}
        {view === 'settings' && (
          <SettingsPage planId={selectedPlanId} onRuleChanged={reclassifyRows} onReceiptsFetched={refreshActiveRows} />
        )}

        {/* DASHBOARD */}
        {view === 'dashboard' && (
          <MetricsDashboard planId={selectedPlanId} />
        )}

        {/* RECEIPTS AND SPENDING */}
        {view === 'receipts' && (
          <ReceiptsPage key={openReceiptId ?? 'receipts'} planId={selectedPlanId} categoryGroups={categoryGroups}
            openReceiptId={openReceiptId} />
        )}
      </main>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState(!!supabase);

  useEffect(() => {
    // No Supabase configured (local dev): skip auth; authLoading already starts false
    if (!supabase) return;

    supabase.auth.getSession().then(({ data: { session: s } }) => {
      setSession(s);
      setAuthLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, s) => {
      if (!s) queryClient.clear();
      setSession(s);
      setAuthLoading(false);
    });

    return () => subscription.unsubscribe();
  }, []);

  if (authLoading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-base)' }}>
        <Loader2 size={32} className="loading-pulse" style={{ color: 'var(--accent)' }} />
      </div>
    );
  }

  // Require auth when Supabase is configured
  if (supabase && !session) {
    return <LoginPage onLogin={() => {}} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <AppContent onLogout={supabase ? () => { supabase!.auth.signOut(); } : undefined} />
    </QueryClientProvider>
  );
}

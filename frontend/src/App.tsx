import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  Workflow, RefreshCw, Loader2, ArrowLeft, Download, Upload as UploadIcon,
  CheckCircle, AlertCircle, XCircle, Settings, BarChart3,
} from 'lucide-react';
import { fetchPlans, uploadCSV, syncBudgets, syncPlanData, fetchAccounts, fetchCategories, fetchPayees, writeBack, createRule, reclassify } from './api/client';
import type { Plan, PredictionRow, Account, CategoryGroup, PayeeItem, WriteBackResponse } from './api/client';
import FileDrop from './components/FileDrop';
import StatsBar from './components/StatsBar';
import ReviewTable, { toReviewedRows, type ReviewedRow, type ReviewStatus } from './components/ReviewTable';
import SettingsPage from './components/SettingsPage';
import MetricsDashboard from './components/MetricsDashboard';

const queryClient = new QueryClient();

type View = 'import' | 'review' | 'push' | 'settings' | 'dashboard';

function AppContent() {
  const [selectedPlanId, setSelectedPlanId] = useState<string>('');
  const [predictions, setPredictions] = useState<PredictionRow[]>(() => {
    try { return JSON.parse(localStorage.getItem('ynab_flow_predictions') || '[]'); } catch { return []; }
  });
  const [reviewRows, setReviewRows] = useState<ReviewedRow[]>(() => {
    try { return JSON.parse(localStorage.getItem('ynab_flow_review_rows') || '[]'); } catch { return []; }
  });
  const [view, setView] = useState<View>('import');
  const [selectedAccountId, setSelectedAccountId] = useState<string>('');
  const [writeMode, setWriteMode] = useState<string>('dry_run');
  const [writeResult, setWriteResult] = useState<WriteBackResponse | null>(null);
  const [ruleToast, setRuleToast] = useState<string | null>(null);
  const [isReclassifying, setIsReclassifying] = useState(false);
  const [changedRows, setChangedRows] = useState<Set<number>>(new Set());

  // Persist import data across reloads
  useEffect(() => {
    localStorage.setItem('ynab_flow_predictions', JSON.stringify(predictions));
  }, [predictions]);
  useEffect(() => {
    localStorage.setItem('ynab_flow_review_rows', JSON.stringify(reviewRows));
  }, [reviewRows]);

  // Clear persisted data when all rows are reviewed (none pending)
  useEffect(() => {
    if (reviewRows.length > 0 && reviewRows.every((r) => r.status !== 'pending')) {
      localStorage.removeItem('ynab_flow_predictions');
      localStorage.removeItem('ynab_flow_review_rows');
    }
  }, [reviewRows]);

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
      return uploadCSV(selectedPlanId, file);
    },
    onSuccess: (data) => {
      setPredictions(data.predictions);
      setReviewRows(toReviewedRows(data.predictions));
      setView('review');
    },
  });

  const writeMutation = useMutation({
    mutationFn: () => {
      const accepted = reviewRows.filter((r) => r.status === 'accepted');
      const txns = accepted.map((r) => ({
        date: r.date,
        amount: r.amount,
        payee_name: r.editedPayee,
        category_name: r.editedCategory,
        memo: r.original_memo,
        account_id: selectedAccountId,
      }));
      return writeBack(selectedPlanId, writeMode, txns);
    },
    onSuccess: (data) => {
      setWriteResult(data);
    },
  });

  const plans: Plan[] = plansQuery.data || [];
  const accounts: Account[] = (accountsQuery.data || []).filter((a) => !a.closed);
  const categoryGroups: CategoryGroup[] = categoriesQuery.data || [];
  const payees: PayeeItem[] = payeesQuery.data || [];

  if (plans.length > 0 && !selectedPlanId) {
    setSelectedPlanId(plans[0].id);
  }
  if (accounts.length > 0 && !selectedAccountId) {
    setSelectedAccountId(accounts[0].id);
  }

  /* ---- Review callbacks ---- */
  const handleUpdateRow = useCallback((index: number, update: Partial<ReviewedRow>) => {
    setReviewRows((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...update };
      return next;
    });
  }, []);

  const handleBulkAction = useCallback((indices: number[], status: ReviewStatus) => {
    setReviewRows((prev) => {
      const next = [...prev];
      indices.forEach((i) => {
        next[i] = { ...next[i], status };
      });
      return next;
    });
  }, []);

  /* ---- Re-classify current rows through the pipeline ---- */
  const reclassifyRows = useCallback(async () => {
    if (!selectedPlanId || reviewRows.length === 0) return;
    setIsReclassifying(true);
    try {
      const txns = reviewRows.map((r) => ({
        date: r.date,
        memo: r.original_memo,
        amount: r.amount,
        label: r.label,
        source_category: r.source_category,
      }));
      const result = await reclassify(selectedPlanId, txns);
      setPredictions(result.predictions);
      setReviewRows((prev) => {
        const updated = toReviewedRows(result.predictions);
        const changed = new Set<number>();
        const merged = updated.map((newRow, i) => {
          const old = prev[i];
          if (!old) return newRow;
          // Track rows where classification changed
          if (old.payee !== newRow.payee || old.category !== newRow.category || old.source !== newRow.source) {
            changed.add(newRow.row_index);
          }
          // Keep user overrides if they manually accepted/ignored or edited fields
          if (old.status === 'ignored') return { ...newRow, status: 'ignored' as const };
          if (old.editedPayee !== (old.payee || '') || old.editedCategory !== (old.category || '')) {
            return { ...newRow, status: old.status, editedPayee: old.editedPayee, editedCategory: old.editedCategory };
          }
          return newRow;
        });
        // Set changed rows for highlighting (clear after animation)
        if (changed.size > 0) {
          setChangedRows(changed);
          setTimeout(() => setChangedRows(new Set()), 3000);
        }
        return merged;
      });
      const count = reviewRows.length;
      setRuleToast(`Reclassification complete — ${count} transactions re-evaluated`);
      setTimeout(() => setRuleToast(null), 2000);
    } finally {
      setIsReclassifying(false);
    }
  }, [selectedPlanId, reviewRows]);

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
    if (view === 'push') { setView('review'); setWriteResult(null); }
    else if (view === 'review') setView('import');
    else if (view === 'settings') setView('import');
    else if (view === 'dashboard') setView('import');
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
          >
            {syncMutation.isPending ? (
              <Loader2 size={16} className="loading-pulse" />
            ) : (
              <RefreshCw size={16} />
            )}
            Sync
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
              <FileDrop
                onFile={(file) => uploadMutation.mutate(file)}
                disabled={uploadMutation.isPending || !selectedPlanId}
              />
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
                  <h2>Last Import ({predictions.length} transactions)</h2>
                  <button className="btn btn-primary" onClick={() => setView('review')}>
                    Review & Approve →
                  </button>
                </div>
                <StatsBar predictions={predictions} />
              </section>
            )}
            {predictions.length === 0 && !uploadMutation.isPending && (
              <section className="animate-in" style={{ textAlign: 'center', padding: '64px 0', color: 'var(--text-muted)' }}>
                <Workflow size={64} style={{ color: 'var(--accent-subtle)', marginBottom: 16 }} />
                <p style={{ fontSize: '1rem' }}>Upload a CSV file to classify your transactions</p>
                <p style={{ fontSize: '0.85rem', marginTop: 4 }}>Supports PostFinance / BCGE export format</p>
              </section>
            )}
          </>
        )}

        {/* REVIEW */}
        {view === 'review' && (
          <section className="animate-in">
            <h2 style={{ marginBottom: 16 }}>Review Transactions</h2>
            <StatsBar predictions={predictions} />
            <ReviewTable
              rows={reviewRows}
              categoryGroups={categoryGroups}
              payees={payees}
              onUpdateRow={handleUpdateRow}
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
                <div className="select-wrapper">
                  <select value={selectedAccountId} onChange={(e) => setSelectedAccountId(e.target.value)}>
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>{a.name} ({a.type})</option>
                    ))}
                  </select>
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
              </div>
              <div style={{
                padding: 16, borderRadius: 'var(--radius-md)', background: 'var(--bg-surface)',
                marginBottom: 24, fontSize: '0.9rem',
              }}>
                <strong style={{ color: 'var(--text-primary)' }}>{acceptedCount}</strong>
                <span style={{ color: 'var(--text-secondary)' }}> accepted transactions → </span>
                <strong style={{ color: 'var(--text-primary)' }}>
                  {accounts.find((a) => a.id === selectedAccountId)?.name || '—'}
                </strong>
              </div>
              <button
                className="btn btn-primary"
                style={{ width: '100%', justifyContent: 'center', padding: '14px 24px' }}
                onClick={() => writeMutation.mutate()}
                disabled={writeMutation.isPending || !selectedAccountId}
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
                      {writeResult.mode === 'dry_run' ? writeResult.total - writeResult.errors : writeResult.created}
                    </div>
                    <div className="stat-label">{writeResult.mode === 'dry_run' ? 'Valid' : 'Created'}</div>
                  </div>
                  {writeResult.skipped > 0 && (
                    <div className="card stat-card">
                      <div className="stat-value" style={{ color: 'var(--warning)' }}>
                        <AlertCircle size={18} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                        {writeResult.skipped}
                      </div>
                      <div className="stat-label">Skipped</div>
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
                        <tr><th>#</th><th>Date</th><th>Payee</th><th>Amount</th><th>Status</th><th>YNAB ID</th></tr>
                      </thead>
                      <tbody>
                        {writeResult.results.map((r) => (
                          <tr key={r.index}>
                            <td>{r.index + 1}</td>
                            <td>{r.date}</td>
                            <td>{r.payee_name}</td>
                            <td className={`amount ${r.amount >= 0 ? 'positive' : 'negative'}`}>
                              {r.amount >= 0 ? '+' : ''}{r.amount.toFixed(2)}
                            </td>
                            <td>
                              <span className={`badge ${
                                r.status === 'created' || r.status === 'dry_run_ok' ? 'badge-success' :
                                r.status === 'skipped' ? 'badge-warning' : 'badge-danger'
                              }`}>
                                {r.status.replace('_', ' ')}
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
          <SettingsPage planId={selectedPlanId} onRuleChanged={reclassifyRows} />
        )}

        {/* DASHBOARD */}
        {view === 'dashboard' && (
          <MetricsDashboard planId={selectedPlanId} />
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  );
}

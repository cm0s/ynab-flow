import { useState, useCallback } from 'react';
import { useQuery, useMutation, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Workflow, RefreshCw, Loader2, ArrowLeft, Download } from 'lucide-react';
import { fetchPlans, uploadCSV, syncBudgets } from './api/client';
import type { Plan, PredictionRow } from './api/client';
import FileDrop from './components/FileDrop';
import StatsBar from './components/StatsBar';
import ReviewTable, { toReviewedRows, type ReviewedRow, type ReviewStatus } from './components/ReviewTable';

const queryClient = new QueryClient();

function AppContent() {
  const [selectedPlanId, setSelectedPlanId] = useState<string>('');
  const [predictions, setPredictions] = useState<PredictionRow[]>([]);
  const [reviewRows, setReviewRows] = useState<ReviewedRow[]>([]);
  const [view, setView] = useState<'import' | 'review'>('import');

  const plansQuery = useQuery({ queryKey: ['plans'], queryFn: fetchPlans });

  const syncMutation = useMutation({
    mutationFn: syncBudgets,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans'] });
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

  const plans: Plan[] = plansQuery.data || [];

  // Auto-select first plan
  if (plans.length > 0 && !selectedPlanId) {
    setSelectedPlanId(plans[0].id);
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

  return (
    <div className="app-layout">
      {/* ---- Header ---- */}
      <header className="app-header">
        <div className="app-header-title">
          {view === 'review' && (
            <button
              className="btn btn-ghost"
              style={{ padding: '4px 8px', marginRight: 4 }}
              onClick={() => setView('import')}
            >
              <ArrowLeft size={18} />
            </button>
          )}
          <Workflow className="logo" size={28} />
          <span>YNAB Flow</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {plans.length > 0 && (
            <div className="select-wrapper">
              <select
                value={selectedPlanId}
                onChange={(e) => setSelectedPlanId(e.target.value)}
              >
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}
          {view === 'review' && (
            <button className="btn btn-primary" onClick={handleExport}>
              <Download size={16} /> Export CSV
            </button>
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
            Sync YNAB
          </button>
        </div>
      </header>

      {/* ---- Main ---- */}
      <main className="app-main">
        {view === 'import' && (
          <>
            {/* File upload */}
            <section style={{ marginBottom: 32 }} className="animate-in">
              <h2 style={{ marginBottom: 16 }}>Import Transactions</h2>
              <FileDrop
                onFile={(file) => uploadMutation.mutate(file)}
                disabled={uploadMutation.isPending || !selectedPlanId}
              />
              {uploadMutation.isPending && (
                <p style={{ textAlign: 'center', marginTop: 16, color: 'var(--text-muted)' }}
                   className="loading-pulse">
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

            {/* Previously loaded results */}
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

            {/* Empty state */}
            {predictions.length === 0 && !uploadMutation.isPending && (
              <section className="animate-in" style={{ textAlign: 'center', padding: '64px 0', color: 'var(--text-muted)' }}>
                <Workflow size={64} style={{ color: 'var(--accent-subtle)', marginBottom: 16 }} />
                <p style={{ fontSize: '1rem' }}>Upload a CSV file to classify your transactions</p>
                <p style={{ fontSize: '0.85rem', marginTop: 4 }}>Supports PostFinance / BCGE export format</p>
              </section>
            )}
          </>
        )}

        {view === 'review' && (
          <section className="animate-in">
            <h2 style={{ marginBottom: 16 }}>Review Transactions</h2>
            <StatsBar predictions={predictions} />
            <ReviewTable
              rows={reviewRows}
              onUpdateRow={handleUpdateRow}
              onBulkAction={handleBulkAction}
            />
          </section>
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

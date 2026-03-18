import { useState } from 'react';
import { useQuery, useMutation, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Workflow, RefreshCw, Loader2 } from 'lucide-react';
import { fetchPlans, uploadCSV, syncBudgets } from './api/client';
import type { Plan, PredictionRow } from './api/client';
import FileDrop from './components/FileDrop';
import ResultsTable from './components/ResultsTable';
import StatsBar from './components/StatsBar';

const queryClient = new QueryClient();

function AppContent() {
  const [selectedPlanId, setSelectedPlanId] = useState<string>('');
  const [predictions, setPredictions] = useState<PredictionRow[]>([]);

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
    },
  });

  const plans: Plan[] = plansQuery.data || [];

  // Auto-select first plan
  if (plans.length > 0 && !selectedPlanId) {
    setSelectedPlanId(plans[0].id);
  }

  return (
    <div className="app-layout">
      {/* ---- Header ---- */}
      <header className="app-header">
        <div className="app-header-title">
          <Workflow className="logo" size={28} />
          <span>YNAB Flow</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
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
        {/* Step 1: File upload */}
        <section style={{ marginBottom: 32 }} className="animate-in">
          <h2 style={{ marginBottom: 16 }}>
            Import Transactions
          </h2>
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

        {/* Step 2: Results */}
        {predictions.length > 0 && (
          <section className="animate-in" style={{ animationDelay: '100ms' }}>
            <h2 style={{ marginBottom: 16 }}>
              Classification Results
            </h2>
            <StatsBar predictions={predictions} />
            <ResultsTable predictions={predictions} />
          </section>
        )}

        {/* Empty state */}
        {predictions.length === 0 && !uploadMutation.isPending && (
          <section
            className="animate-in"
            style={{
              textAlign: 'center',
              padding: '64px 0',
              color: 'var(--text-muted)',
            }}
          >
            <Workflow size={64} style={{ color: 'var(--accent-subtle)', marginBottom: 16 }} />
            <p style={{ fontSize: '1rem' }}>
              Upload a CSV file to classify your transactions
            </p>
            <p style={{ fontSize: '0.85rem', marginTop: 4 }}>
              Supports PostFinance / BCGE export format
            </p>
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

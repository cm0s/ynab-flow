import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings as SettingsIcon, Key, Gauge, Shield, RefreshCw, Loader2, Brain, Database } from 'lucide-react';
import { syncBudgets, syncPlanData, trainModels, fetchCategories, fetchPayees, fetchModels, activateModel } from '../api/client';
import type { CategoryGroup, PayeeItem, ModelVersionInfo, TargetScore } from '../api/client';
import RulesManager from './RulesManager';
import MailboxSettings from './MailboxSettings';
import AISettings from './AISettings';

interface Props {
  planId: string;
  onRuleChanged?: () => void;
  onReceiptsFetched?: () => void;
}

export default function SettingsPage({ planId, onRuleChanged, onReceiptsFetched }: Props) {
  const queryClient = useQueryClient();
  const [autoApproveThreshold, setAutoApproveThreshold] = useState(95);
  const [reviewThreshold, setReviewThreshold] = useState(75);
  const [syncProgress, setSyncProgress] = useState('Starting sync…');
  const [tab, setTab] = useState<'general' | 'rules'>('general');

  const categoriesQuery = useQuery({
    queryKey: ['categories', planId],
    queryFn: () => fetchCategories(planId),
    enabled: !!planId,
  });
  const payeesQuery = useQuery({
    queryKey: ['payees', planId],
    queryFn: () => fetchPayees(planId),
    enabled: !!planId,
  });
  const categoryGroups: CategoryGroup[] = categoriesQuery.data || [];
  const payees: PayeeItem[] = payeesQuery.data || [];

  const syncMutation = useMutation({
    mutationFn: syncBudgets,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['plans'] }),
  });

  const fullSyncMutation = useMutation({
    mutationFn: () => {
      setSyncProgress('Starting sync…');
      return syncPlanData(planId, true, setSyncProgress);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['metrics', planId] });
      queryClient.invalidateQueries({ queryKey: ['accounts', planId] });
      queryClient.invalidateQueries({ queryKey: ['categories', planId] });
      queryClient.invalidateQueries({ queryKey: ['payees', planId] });
    },
  });

  const modelsQuery = useQuery({ queryKey: ['models'], queryFn: fetchModels });

  const trainMutation = useMutation({
    mutationFn: () => trainModels(planId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['models'] }),
  });

  const restoreMutation = useMutation({
    mutationFn: (versionId: string) => activateModel(versionId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['models'] }),
  });

  const versions = modelsQuery.data?.versions ?? [];
  const activeModel = versions.find((v) => v.status === 'active');
  const lastTraining = versions.find((v) => v.status !== 'retired');
  const previousModel = versions
    .filter((v) => v.status === 'retired' && v.files_present)
    .sort((a, b) => (b.activated_at ?? '').localeCompare(a.activated_at ?? ''))[0];

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '10px 14px', background: 'var(--bg-card)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
    color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.875rem',
    outline: 'none',
  };

  return (
    <div className="animate-in">
      <h2 style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 10 }}>
        <SettingsIcon size={24} color="var(--accent)" /> Settings
      </h2>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 24 }}>
        <button
          className={`btn ${tab === 'general' ? 'btn-secondary' : 'btn-ghost'}`}
          onClick={() => setTab('general')}
        >
          <Gauge size={16} /> General
        </button>
        <button
          className={`btn ${tab === 'rules' ? 'btn-secondary' : 'btn-ghost'}`}
          onClick={() => setTab('rules')}
        >
          <Shield size={16} /> Rules
        </button>
      </div>

      {tab === 'general' && (
        <div style={{ maxWidth: 600, display: 'flex', flexDirection: 'column', gap: 24 }}>
          {/* API Connection */}
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Key size={18} color="var(--accent)" /> YNAB Connection
            </h3>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', marginBottom: 6, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                API Token
              </label>
              <input
                type="password"
                style={inputStyle}
                placeholder="••••••••••••••••••••"
                disabled
              />
              <p style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                API token is managed via the backend .env file for security.
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
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
                Refresh budget list
              </button>
            </div>
            <p style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              Updates the list of budgets and their accounts. The Sync button in the header also does this,
              then fetches recent changes to transactions.
            </p>
            {syncMutation.isSuccess && (
              <p style={{ marginTop: 8, color: 'var(--success)', fontSize: '0.85rem' }}>
                ✓ Budget list refreshed
              </p>
            )}
          </div>

          {/* Data Sync — full sync with transactions */}
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Database size={18} color="var(--accent)" /> Re-download all history
            </h3>
            <p style={{ marginBottom: 16, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              For everyday use, the <strong>Sync</strong> button in the header is enough: it only fetches what changed.
              This downloads <strong>all categories, payees and transactions</strong> of the selected budget again.
              It is slow, so use it when local data looks wrong or incomplete, or after an update that records new
              information for every transaction.
            </p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                className="btn btn-primary"
                onClick={() => fullSyncMutation.mutate()}
                disabled={fullSyncMutation.isPending || !planId}
              >
                {fullSyncMutation.isPending ? (
                  <><Loader2 size={16} className="loading-pulse" /> {syncProgress}</>
                ) : (
                  <><Database size={16} /> Re-download all history</>
                )}
              </button>
            </div>
            {fullSyncMutation.isSuccess && (
              <p style={{ marginTop: 8, color: 'var(--success)', fontSize: '0.85rem' }}>
                ✓ All history re-downloaded and available for classification
              </p>
            )}
            {fullSyncMutation.isError && (
              <p style={{ marginTop: 8, color: 'var(--danger)', fontSize: '0.85rem' }}>
                Error: {(fullSyncMutation.error as Error).message}
              </p>
            )}
          </div>

          <MailboxSettings onFetched={onReceiptsFetched} />
          <AISettings />

          {/* ML Training */}
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Brain size={18} color="var(--accent)" /> ML Model Training
            </h3>
            <p style={{ marginBottom: 16, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Train the machine learning model from your synced YNAB transaction history.
              Run this <strong>after syncing</strong> to enable ML-based auto-classification.
            </p>
            <button
              className="btn btn-secondary"
              onClick={() => trainMutation.mutate()}
              disabled={trainMutation.isPending || !planId}
            >
              {trainMutation.isPending ? (
                <><Loader2 size={16} className="loading-pulse" /> Training…</>
              ) : (
                <><Brain size={16} /> Train ML Model</>
              )}
            </button>
            {trainMutation.isSuccess && (
              <p style={{ marginTop: 8, color: trainMutation.data.promoted ? 'var(--success)' : 'var(--warning)', fontSize: '0.85rem' }}>
                {trainMutation.data.promoted ? '✓ ' : ''}{trainMutation.data.note}
              </p>
            )}
            <ModelStatus
              active={activeModel}
              last={lastTraining}
              previous={previousModel}
              restoring={restoreMutation.isPending}
              onRestore={(id) => restoreMutation.mutate(id)}
              error={restoreMutation.isError ? (restoreMutation.error as Error).message : null}
            />
            {trainMutation.isError && (
              <p style={{ marginTop: 8, color: 'var(--danger)', fontSize: '0.85rem' }}>
                Error: {(trainMutation.error as Error).message}
              </p>
            )}
          </div>

          {/* Confidence Thresholds */}
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Gauge size={18} color="var(--accent)" /> Confidence Thresholds
            </h3>

            <div style={{ marginBottom: 20 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  Auto-approve threshold
                </label>
                <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--success)' }}>
                  {autoApproveThreshold}%
                </span>
              </div>
              <input
                type="range"
                min={50} max={100}
                value={autoApproveThreshold}
                onChange={(e) => setAutoApproveThreshold(Number(e.target.value))}
                style={{ width: '100%', accentColor: 'var(--success)' }}
              />
              <p style={{ marginTop: 4, fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Predictions above this confidence are automatically accepted.
              </p>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  Review threshold
                </label>
                <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--warning)' }}>
                  {reviewThreshold}%
                </span>
              </div>
              <input
                type="range"
                min={30} max={95}
                value={reviewThreshold}
                onChange={(e) => setReviewThreshold(Number(e.target.value))}
                style={{ width: '100%', accentColor: 'var(--warning)' }}
              />
              <p style={{ marginTop: 4, fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Fuzzy matches below this confidence are discarded.
              </p>
            </div>
          </div>
        </div>
      )}

      {tab === 'rules' && (
        <RulesManager planId={planId} categoryGroups={categoryGroups} payees={payees} onRuleChanged={onRuleChanged} />
      )}
    </div>
  );
}

const pct = (s?: TargetScore | null) => (s ? `${(s.accuracy * 100).toFixed(1)}%` : '—');
const day = (iso?: string | null) => (iso ? iso.slice(0, 10) : '—');

/** Active model version, the last training comparison and a restore action. */
function ModelStatus({ active, last, previous, restoring, onRestore, error }: {
  active?: ModelVersionInfo;
  last?: ModelVersionInfo;
  previous?: ModelVersionInfo;
  restoring: boolean;
  onRestore: (id: string) => void;
  error: string | null;
}) {
  const muted: React.CSSProperties = { color: 'var(--text-muted)', fontSize: '0.8rem' };
  const cmp = last?.comparison;
  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)', fontSize: '0.85rem' }}>
      <div style={{ marginBottom: 8 }}>
        <strong>Active model:</strong>{' '}
        {active ? <>{active.id} <span style={muted}>· since {day(active.activated_at)}
          {active.training.training_rows != null && ` · ${active.training.training_rows} examples`}</span></> : 'none'}
      </div>
      {last && cmp?.candidate && (
        <div style={{ marginBottom: 8 }}>
          <strong>Last training</strong> <span style={muted}>({day(last.created_at)}, {last.status},
            held out {cmp.holdout_examples ?? 0} recent examples)</span>
          <table style={{ marginTop: 6, fontSize: '0.8rem' }}>
            <thead><tr><th style={{ textAlign: 'left', paddingRight: 16 }}></th><th style={{ paddingRight: 16 }}>New</th><th>Previous</th></tr></thead>
            <tbody>
              {(['payee', 'category'] as const).map((t) => (
                <tr key={t}>
                  <td style={{ paddingRight: 16 }}>{t}</td>
                  <td style={{ paddingRight: 16 }}>{pct(cmp.candidate?.[t])}</td>
                  <td>{pct(cmp.active?.[t])}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ ...muted, marginTop: 6 }}>{last.note}</p>
        </div>
      )}
      {previous && (
        <button className="btn btn-ghost" disabled={restoring} onClick={() => onRestore(previous.id)}>
          {restoring ? <Loader2 size={14} className="loading-pulse" /> : <RefreshCw size={14} />} Restore previous ({previous.id})
        </button>
      )}
      {error && <p style={{ color: 'var(--danger)', marginTop: 6 }}>{error}</p>}
    </div>
  );
}

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings as SettingsIcon, Key, Gauge, Shield, RefreshCw, Loader2, Brain, Database } from 'lucide-react';
import { syncBudgets, syncPlanData, trainModels, fetchCategories, fetchPayees } from '../api/client';
import type { CategoryGroup, PayeeItem } from '../api/client';
import RulesManager from './RulesManager';

interface Props {
  planId: string;
}

export default function SettingsPage({ planId }: Props) {
  const queryClient = useQueryClient();
  const [autoApproveThreshold, setAutoApproveThreshold] = useState(95);
  const [reviewThreshold, setReviewThreshold] = useState(75);
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
    mutationFn: () => syncPlanData(planId, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['metrics', planId] });
      queryClient.invalidateQueries({ queryKey: ['accounts', planId] });
    },
  });

  const trainMutation = useMutation({
    mutationFn: () => trainModels(planId),
  });

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
                Sync Budgets
              </button>
            </div>
            {syncMutation.isSuccess && (
              <p style={{ marginTop: 8, color: 'var(--success)', fontSize: '0.85rem' }}>
                ✓ Budget sync completed
              </p>
            )}
          </div>

          {/* Data Sync — full sync with transactions */}
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Database size={18} color="var(--accent)" /> Historical Data Sync
            </h3>
            <p style={{ marginBottom: 16, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Syncs <strong>categories, payees, and all transactions</strong> from YNAB for the selected plan.
              This data is used by the historical matcher and ML classifier to auto-classify new imports.
            </p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                className="btn btn-primary"
                onClick={() => fullSyncMutation.mutate()}
                disabled={fullSyncMutation.isPending || !planId}
              >
                {fullSyncMutation.isPending ? (
                  <><Loader2 size={16} className="loading-pulse" /> Syncing transactions…</>
                ) : (
                  <><Database size={16} /> Full Sync (Categories + Payees + Transactions)</>
                )}
              </button>
            </div>
            {fullSyncMutation.isSuccess && (
              <p style={{ marginTop: 8, color: 'var(--success)', fontSize: '0.85rem' }}>
                ✓ Full sync completed — historical transactions are now available for classification
              </p>
            )}
            {fullSyncMutation.isError && (
              <p style={{ marginTop: 8, color: 'var(--danger)', fontSize: '0.85rem' }}>
                Error: {(fullSyncMutation.error as Error).message}
              </p>
            )}
          </div>

          {/* ML Training */}
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Brain size={18} color="var(--accent)" /> ML Model Training
            </h3>
            <p style={{ marginBottom: 16, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Train the machine learning model from your synced YNAB transaction history.
              Run this <strong>after a full sync</strong> to enable ML-based auto-classification.
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
              <p style={{ marginTop: 8, color: 'var(--success)', fontSize: '0.85rem' }}>
                ✓ ML model trained successfully
              </p>
            )}
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
        <RulesManager planId={planId} categoryGroups={categoryGroups} payees={payees} />
      )}
    </div>
  );
}

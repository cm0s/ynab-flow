import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings as SettingsIcon, Key, Gauge, Shield, RefreshCw, Loader2 } from 'lucide-react';
import { syncBudgets } from '../api/client';
import RulesManager from './RulesManager';

interface Props {
  planId: string;
}

export default function SettingsPage({ planId }: Props) {
  const queryClient = useQueryClient();
  const [autoApproveThreshold, setAutoApproveThreshold] = useState(95);
  const [reviewThreshold, setReviewThreshold] = useState(75);
  const [tab, setTab] = useState<'general' | 'rules'>('general');

  const syncMutation = useMutation({
    mutationFn: syncBudgets,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['plans'] }),
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
              Sync All Data from YNAB
            </button>
            {syncMutation.isSuccess && (
              <p style={{ marginTop: 8, color: 'var(--success)', fontSize: '0.85rem' }}>
                ✓ Sync completed successfully
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
        <RulesManager planId={planId} />
      )}
    </div>
  );
}

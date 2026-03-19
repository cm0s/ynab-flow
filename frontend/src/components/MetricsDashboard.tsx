import { useQuery } from '@tanstack/react-query';
import {
  BarChart3, Database, Zap, Users, Tag, Layers,
} from 'lucide-react';

interface MetricsData {
  plan_name: string;
  total_transactions: number;
  total_rules: number;
  total_accounts: number;
  total_categories: number;
  total_payees: number;
  training_set_size: number;
  top_categories: { name: string; count: number }[];
}

async function fetchMetrics(planId: string): Promise<MetricsData> {
  const res = await fetch(`/api/metrics?plan_id=${planId}`);
  if (!res.ok) throw new Error('Failed to load metrics');
  return res.json();
}

interface Props {
  planId: string;
}

export default function MetricsDashboard({ planId }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['metrics', planId],
    queryFn: () => fetchMetrics(planId),
    enabled: !!planId,
  });

  if (isLoading) {
    return (
      <div className="animate-in" style={{ textAlign: 'center', padding: 48, color: 'var(--text-muted)' }}>
        Loading metrics…
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="animate-in" style={{ textAlign: 'center', padding: 48, color: 'var(--text-muted)' }}>
        <BarChart3 size={48} style={{ color: 'var(--accent-subtle)', marginBottom: 12 }} />
        <p>No metrics available yet. Sync your YNAB data first.</p>
      </div>
    );
  }

  const maxCount = data.top_categories.length > 0 ? data.top_categories[0].count : 1;

  return (
    <div className="animate-in">
      <h2 style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 10 }}>
        <BarChart3 size={24} color="var(--accent)" /> Dashboard
      </h2>

      {/* Stats grid */}
      <div className="stats-row" style={{ marginBottom: 32 }}>
        <div className="card stat-card">
          <div className="stat-value" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Database size={20} color="var(--accent)" />
            {data.total_transactions.toLocaleString()}
          </div>
          <div className="stat-label">Historical Transactions</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Zap size={20} color="var(--warning)" />
            {data.total_rules}
          </div>
          <div className="stat-label">Active Rules</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Layers size={20} color="var(--info)" />
            {data.total_accounts}
          </div>
          <div className="stat-label">Accounts</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Tag size={20} color="var(--success)" />
            {data.total_categories}
          </div>
          <div className="stat-label">Categories</div>
        </div>
        <div className="card stat-card">
          <div className="stat-value" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Users size={20} color="var(--accent-light)" />
            {data.total_payees}
          </div>
          <div className="stat-label">Payees</div>
        </div>
      </div>

      {/* Top categories bar chart */}
      {data.top_categories.length > 0 && (
        <div className="card" style={{ padding: 24 }}>
          <h3 style={{ marginBottom: 20 }}>Top Categories by Transaction Count</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.top_categories.map((cat) => (
              <div key={cat.name} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{
                  width: 140, fontSize: '0.8rem', color: 'var(--text-secondary)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  flexShrink: 0, textAlign: 'right',
                }}>
                  {cat.name}
                </div>
                <div style={{
                  flex: 1, height: 24, background: 'var(--bg-surface)',
                  borderRadius: 'var(--radius-sm)', overflow: 'hidden',
                }}>
                  <div style={{
                    height: '100%',
                    width: `${(cat.count / maxCount) * 100}%`,
                    background: 'linear-gradient(90deg, var(--accent), var(--accent-light))',
                    borderRadius: 'var(--radius-sm)',
                    transition: 'width 0.6s ease',
                    minWidth: 2,
                  }} />
                </div>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', width: 40, textAlign: 'right' }}>
                  {cat.count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

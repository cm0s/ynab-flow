import { useQuery } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { fetchAIUsage } from '../api/client';

/** AI on/off reason, model, and this month's usage against the budget. */
export default function AISettings() {
  const usage = useQuery({ queryKey: ['aiUsage'], queryFn: fetchAIUsage });
  const data = usage.data;
  const share = data && data.budget_usd > 0 ? Math.min(1, data.spent_usd / data.budget_usd) : 0;

  return (
    <div className="card" style={{ padding: 24 }}>
      <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Sparkles size={18} color="var(--accent)" /> AI suggestions
      </h3>
      <p style={{ marginBottom: 12, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
        Claude suggests payees, categories and splits for rows that rules and history cannot settle, and reads
        receipts. It runs only when you ask, never overrides your edits, rules or confident history, and its
        suggestions always need review. The key, model and monthly budget are set in <code>backend/.env</code>.
      </p>
      {usage.isError && <p style={{ color: 'var(--danger)', fontSize: '0.85rem' }}>{(usage.error as Error).message}</p>}
      {data && (
        <>
          <p style={{ fontSize: '0.85rem', color: data.feature.enabled ? 'var(--success)' : 'var(--text-muted)', marginBottom: 8 }}>
            {data.feature.enabled ? `On · model ${data.model}` : data.feature.reason}
          </p>
          <div style={{ fontSize: '0.85rem', marginBottom: 6 }}>
            This month: <strong>${data.spent_usd.toFixed(2)}</strong> of ${data.budget_usd.toFixed(2)}
            {' '}({Object.entries(data.requests).map(([k, v]) => `${v} ${k === 'extract' ? 'receipt readings' : 'suggestions'}`).join(', ') || 'no requests'})
          </div>
          <div className="confidence-bar" style={{ width: '100%', height: 6 }}>
            <div className={`confidence-bar-fill ${share >= 1 ? 'low' : share >= 0.8 ? 'medium' : 'high'}`}
              style={{ width: `${share * 100}%` }} />
          </div>
          <p style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            About ${data.estimate_per_request_usd.toFixed(3)} per suggestion. Requests stop when the budget is reached.
          </p>
        </>
      )}
    </div>
  );
}

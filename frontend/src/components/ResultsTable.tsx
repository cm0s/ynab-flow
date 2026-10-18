import type { PredictionRow } from '../api/client';
import { CheckCircle, AlertTriangle, HelpCircle, Zap, Clock, Brain } from 'lucide-react';

interface Props {
  predictions: PredictionRow[];
}

const sourceIcon = (source: string) => {
  switch (source) {
    case 'rule': return <Zap size={14} />;
    case 'exact_history': return <CheckCircle size={14} />;
    case 'fuzzy_history': return <Clock size={14} />;
    case 'ml': return <Brain size={14} />;
    default: return <HelpCircle size={14} />;
  }
};

const sourceBadge = (source: string) => {
  const map: Record<string, string> = {
    rule: 'badge-accent',
    exact_history: 'badge-success',
    fuzzy_history: 'badge-info',
    ml: 'badge-warning',
    unclassified: 'badge-danger',
  };
  return map[source] || 'badge-danger';
};

const confLevel = (c: number) =>
  c >= 0.95 ? 'high' : c >= 0.75 ? 'medium' : 'low';

function formatAmount(amount: number) {
  const abs = Math.abs(amount).toFixed(2);
  return amount >= 0 ? `+${abs}` : `-${abs}`;
}

export default function ResultsTable({ predictions }: Props) {
  if (predictions.length === 0) return null;

  return (
    <div className="card" style={{ overflow: 'auto', maxHeight: '70vh' }}>
      <table className="results-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Memo</th>
            <th>Amount</th>
            <th>Payee</th>
            <th>Category</th>
            <th>Source</th>
            <th>Confidence</th>
            <th>Review</th>
          </tr>
        </thead>
        <tbody>
          {predictions.map((row, i) => (
            <tr key={i} className="animate-in" style={{ animationDelay: `${i * 20}ms` }}>
              <td style={{ whiteSpace: 'nowrap' }}>{row.date}</td>
              <td title={row.original_memo}>
                <div style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {row.merchant_stem || row.cleaned_memo || row.original_memo}
                </div>
              </td>
              <td className={`amount ${row.amount >= 0 ? 'positive' : 'negative'}`}>
                {formatAmount(row.amount)}
              </td>
              <td style={{ color: row.payee ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {row.payee || '—'}
              </td>
              <td style={{ color: row.category ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {row.category || '—'}
              </td>
              <td>
                <span className={`badge ${sourceBadge(row.source)}`}>
                  {sourceIcon(row.source)} {row.source.replace('_', ' ')}
                </span>
              </td>
              <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div className="confidence-bar">
                    <div
                      className={`confidence-bar-fill ${confLevel(row.confidence)}`}
                      style={{ width: `${row.confidence * 100}%` }}
                    />
                  </div>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    {(row.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </td>
              <td>
                {row.review_required ? (
                  <AlertTriangle size={16} color="var(--warning)" />
                ) : (
                  <CheckCircle size={16} color="var(--success)" />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

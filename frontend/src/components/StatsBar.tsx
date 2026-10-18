import type { PredictionRow } from '../api/client';
import type { ReviewedRow } from './ReviewTable';
import { BarChart3, CheckCircle, AlertTriangle, XCircle } from 'lucide-react';

interface Props {
  predictions: PredictionRow[];
  reviewRows?: ReviewedRow[];
}

export default function StatsBar({ predictions, reviewRows }: Props) {
  if (predictions.length === 0) return null;

  const total = predictions.length;
  const approved = reviewRows
    ? reviewRows.filter(r => r.status === 'accepted').length
    : predictions.filter(p => !p.review_required).length;
  const needsReview = reviewRows
    ? reviewRows.filter(r => r.status === 'pending').length
    : predictions.filter(p => p.review_required).length;
  const unclassified = predictions.filter(p => p.source === 'unclassified').length;
  const avgConf = predictions.reduce((s, p) => s + p.confidence, 0) / total;

  return (
    <div className="stats-row animate-in">
      <div className="card stat-card">
        <div className="stat-value" style={{ color: 'var(--text-primary)' }}>
          <BarChart3 size={20} style={{ verticalAlign: 'middle', marginRight: 8, color: 'var(--accent)' }} />
          {total}
        </div>
        <div className="stat-label">Total Transactions</div>
      </div>
      <div className="card stat-card">
        <div className="stat-value" style={{ color: 'var(--success)' }}>
          <CheckCircle size={20} style={{ verticalAlign: 'middle', marginRight: 8 }} />
          {approved}
        </div>
        <div className="stat-label">Approved</div>
      </div>
      <div className="card stat-card">
        <div className="stat-value" style={{ color: 'var(--warning)' }}>
          <AlertTriangle size={20} style={{ verticalAlign: 'middle', marginRight: 8 }} />
          {needsReview}
        </div>
        <div className="stat-label">Needs Review</div>
      </div>
      <div className="card stat-card">
        <div className="stat-value" style={{ color: 'var(--danger)' }}>
          <XCircle size={20} style={{ verticalAlign: 'middle', marginRight: 8 }} />
          {unclassified}
        </div>
        <div className="stat-label">Unclassified</div>
      </div>
      <div className="card stat-card">
        <div className="stat-value" style={{ color: 'var(--info)' }}>
          {(avgConf * 100).toFixed(0)}%
        </div>
        <div className="stat-label">Avg Confidence</div>
      </div>
    </div>
  );
}

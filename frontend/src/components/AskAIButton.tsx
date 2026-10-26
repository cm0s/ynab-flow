import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles, Loader2 } from 'lucide-react';
import { aiSuggestBatch, fetchAIUsage, type AISuggestResult } from '../api/client';
import { aiEligible } from '../lib/importFlow';
import type { ReviewedRow } from './ReviewTable';

interface Props {
  batchId: string | null;
  rows: ReviewedRow[];
  onDone: (result: AISuggestResult) => void;
}

/** "Ask AI (n rows, ≈ $x)": explicit, user-triggered AI suggestions with a cost estimate up front. */
export default function AskAIButton({ batchId, rows, onDone }: Props) {
  const queryClient = useQueryClient();
  const usage = useQuery({ queryKey: ['aiUsage'], queryFn: fetchAIUsage });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const data = usage.data;
  if (!data || !batchId) return null;
  if (!data.feature.enabled) {
    return <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }} title={data.feature.reason}>AI off</span>;
  }
  const eligible = rows.filter(aiEligible);
  const estimate = eligible.length * data.estimate_per_request_usd;

  const run = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await aiSuggestBatch(batchId, eligible.map((r) => r.id));
      onDone(result);
      const notes = [
        `${result.suggested ?? 0} suggestion(s)`,
        result.invalid ? `${result.invalid} invalid` : '',
        result.refused ? `${result.refused} declined` : '',
        result.error ? `${result.error} failed` : '',
        result.skipped_budget ? `${result.skipped_budget} skipped: monthly budget reached` : '',
      ].filter(Boolean);
      setMessage(`${notes.join(' · ')} — $${result.usage.spent_usd.toFixed(2)} of $${result.usage.budget_usd.toFixed(2)} used this month.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      queryClient.invalidateQueries({ queryKey: ['aiUsage'] });
    }
  };

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <button
        className="btn btn-secondary"
        disabled={busy || eligible.length === 0}
        onClick={run}
        title={`Asks ${data.model} for payee, category and split suggestions. Suggestions always need your review. `
          + `Rows settled by your edits, rules or confident history are skipped. Budget left this month: $${data.remaining_usd.toFixed(2)}.`}
      >
        {busy ? <Loader2 size={14} className="loading-pulse" /> : <Sparkles size={14} />}
        {' '}Ask AI ({eligible.length} row{eligible.length === 1 ? '' : 's'}, ≈ ${estimate.toFixed(2)})
      </button>
      {message && <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{message}</span>}
      {error && <span style={{ fontSize: '0.8rem', color: 'var(--danger)' }}>{error}</span>}
    </span>
  );
}

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Sparkles, ChevronDown, ChevronRight } from 'lucide-react';
import { fetchSpending, estimateCategorizeItems, categorizeItems } from '../api/client';
import type { AIEstimate, SpendingGroup } from '../api/client';
import { formatMilliunits } from '../lib/splits';
import { CATEGORY_SOURCE_LABELS as SOURCE_LABELS, inputStyle } from '../lib/receipts';

const small: React.CSSProperties = { fontSize: '0.78rem', padding: '3px 10px' };

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function money(milliunits: number): string {
  return `${milliunits < 0 ? '-' : ''}${formatMilliunits(milliunits)}`;
}

/** Spending from receipt items by category, merchant or item, per month, with drill-down. Local only. */
export default function SpendingReport({ planId, onOpenReceipt }: { planId: string; onOpenReceipt: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [range, setRange] = useState({ from: isoDaysAgo(90), to: new Date().toISOString().slice(0, 10) });
  const [group, setGroup] = useState<'category' | 'merchant' | 'item'>('category');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<AIEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);

  const report = useQuery({
    queryKey: ['spending', planId, range, group, q],
    queryFn: () => fetchSpending({ plan_id: planId, from: range.from, to: range.to, group, q }),
    enabled: !!planId,
  });
  const data = report.data;
  const max = data ? Math.max(1, ...data.groups.map((g) => Math.abs(g.total_milliunits))) : 1;
  const uncategorizedIds = data?.uncategorized_receipt_ids ?? [];

  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setMessage({ text: (e as Error).message, error: true });
    } finally {
      setBusy(false);
    }
  };
  const askEstimate = () => act(async () => setEstimate(await estimateCategorizeItems(uncategorizedIds, planId)));
  const categorize = () => act(async () => {
    setEstimate(null);
    const result = await categorizeItems(uncategorizedIds, planId);
    setMessage({
      text: `${result.items_filled} item(s) categorized in ${result.receipts} receipt(s)`
        + (result.failed ? `, ${result.failed} failed: ${result.errors.join(' ')}` : '.'),
      error: result.failed > 0,
    });
    queryClient.invalidateQueries({ queryKey: ['spending'] });
    queryClient.invalidateQueries({ queryKey: ['receipt'] });
  });

  return (
    <div className="card" style={{ padding: 16, fontSize: '0.85rem' }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
        <input type="date" style={inputStyle} value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} />
        <span style={{ color: 'var(--text-muted)' }}>to</span>
        <input type="date" style={inputStyle} value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} />
        <select style={inputStyle} value={group} onChange={(e) => { setGroup(e.target.value as typeof group); setOpen(null); }}>
          <option value="category">By category</option>
          <option value="merchant">By merchant</option>
          <option value="item">By item</option>
        </select>
        <input style={{ ...inputStyle, flex: '1 1 140px' }} placeholder="Search items or merchants…" value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>

      {report.isLoading && <p style={{ color: 'var(--text-muted)' }}><Loader2 size={14} className="loading-pulse" /> Loading…</p>}
      {report.isError && <p style={{ color: 'var(--danger)' }}>{(report.error as Error).message}</p>}
      {data && (
        <>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12, color: 'var(--text-secondary)' }}>
            <span><strong style={{ color: 'var(--text-primary)' }}>{data.currency} {money(data.total_milliunits)}</strong> from {data.receipts_counted} receipt(s)</span>
            <span>{(data.uncategorized_share * 100).toFixed(0)}% uncategorized ({data.currency} {money(data.uncategorized_milliunits)})</span>
            {data.uncounted.length > 0 && <span>{data.uncounted.length} receipt(s) not counted (see below)</span>}
          </div>
          {uncategorizedIds.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <button className="btn btn-secondary" style={small} disabled={busy} onClick={askEstimate}>
                <Sparkles size={12} /> Categorize items ({uncategorizedIds.length} receipt(s))
              </button>
              {estimate && (
                <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)', padding: 8, marginTop: 6 }}>
                  {estimate.requests} AI request(s), about ${estimate.estimate_usd.toFixed(3)}
                  {' '}(${estimate.remaining_usd.toFixed(2)} of ${estimate.budget_usd.toFixed(2)} left this month).
                  Only items without your, a split or a bank-row category are sent.
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <button className="btn btn-primary" style={small} disabled={busy || estimate.requests === 0} onClick={categorize}>
                      Categorize
                    </button>
                    <button className="btn btn-ghost" style={small} onClick={() => setEstimate(null)}>Cancel</button>
                  </div>
                </div>
              )}
            </div>
          )}
          {message && <div style={{ color: message.error ? 'var(--danger)' : 'var(--success)', marginBottom: 8 }}>{message.text}</div>}

          <div style={{ overflowX: 'auto' }}>
            <table className="results-table" style={{ tableLayout: 'auto' }}>
              <thead>
                <tr>
                  <th>{group === 'category' ? 'Category' : group === 'merchant' ? 'Merchant' : 'Item'}</th>
                  {data.months.map((m) => <th key={m} style={{ textAlign: 'right' }}>{m}</th>)}
                  <th style={{ textAlign: 'right' }}>Total</th>
                  <th style={{ width: 140 }} />
                </tr>
              </thead>
              <tbody>
                {data.groups.map((g) => (
                  <GroupRows key={g.key || g.label} group={g} months={data.months} max={max}
                    open={open === (g.key || g.label)} onToggle={() => setOpen(open === (g.key || g.label) ? null : (g.key || g.label))}
                    onOpenReceipt={onOpenReceipt} />
                ))}
              </tbody>
            </table>
          </div>
          {data.groups.length === 0 && <p style={{ color: 'var(--text-muted)', padding: 12 }}>No counted receipts in this period.</p>}

          {data.uncounted.length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>Not counted ({data.uncounted.length})</summary>
              {data.uncounted.map((u) => (
                <div key={u.receipt_id} style={{ padding: '4px 0', borderTop: '1px solid var(--border)', cursor: 'pointer' }}
                  onClick={() => onOpenReceipt(u.receipt_id)}>
                  {u.date} · {u.merchant || '(no merchant)'} · {u.total_milliunits != null ? `${u.currency} ${formatMilliunits(u.total_milliunits)}` : '—'}
                  <span style={{ color: 'var(--text-muted)' }}> — {u.reason}</span>
                </div>
              ))}
            </details>
          )}
        </>
      )}
    </div>
  );
}

function GroupRows({ group: g, months, max, open, onToggle, onOpenReceipt }: {
  group: SpendingGroup; months: string[]; max: number; open: boolean; onToggle: () => void; onOpenReceipt: (id: string) => void;
}) {
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: 'pointer' }}>
        <td style={{ color: 'var(--text-primary)' }}>
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} {g.label}
        </td>
        {months.map((m) => <td key={m} style={{ textAlign: 'right' }}>{g.months[m] ? money(g.months[m]) : ''}</td>)}
        <td style={{ textAlign: 'right', color: 'var(--text-primary)' }}>{money(g.total_milliunits)}</td>
        <td>
          <div style={{ height: 8, borderRadius: 4, background: 'var(--accent)', opacity: 0.8,
            width: `${Math.max(2, (Math.abs(g.total_milliunits) / max) * 100)}%` }} />
        </td>
      </tr>
      {open && g.lines.map((l, n) => (
        <tr key={`${l.receipt_id}-${l.item_index ?? 'x'}-${n}`} onClick={() => onOpenReceipt(l.receipt_id)}
          style={{ cursor: 'pointer', fontSize: '0.8rem' }}>
          <td className="no-truncate" style={{ paddingLeft: 28 }}>
            {l.date} · {l.merchant} · {l.description}
            <span style={{ color: 'var(--text-muted)' }}> · {l.category_name ?? 'Uncategorized'} ({SOURCE_LABELS[l.category_source] ?? l.category_source})</span>
          </td>
          {months.map((m) => <td key={m} />)}
          <td style={{ textAlign: 'right' }}>{money(l.amount_milliunits)}</td>
          <td />
        </tr>
      ))}
    </>
  );
}

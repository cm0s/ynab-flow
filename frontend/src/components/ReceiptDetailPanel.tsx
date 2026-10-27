import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { X, Pencil, RotateCcw, Sparkles, Search, Link2, Unlink, Ban, CheckCircle, EyeOff, Eye, Plus, Trash2, Loader2 } from 'lucide-react';
import {
  fetchReceipt, correctReceipt, classifyReceipt, estimateReread, rereadReceipt, receiptCandidates, linkReceiptRow,
  setItemCategory,
} from '../api/client';
import type { AIEstimate, CategoryGroup, ReceiptDetail, ReceiptMatchView } from '../api/client';
import { formatMilliunits, parseMilliunits } from '../lib/splits';
import { CategoryPicker } from './SearchablePicker';
import { CATEGORY_SOURCE_LABELS as SOURCE_LABELS, KIND_LABELS, STATUS_LABELS, inputStyle } from '../lib/receipts';

const FIELD_LABELS: Record<string, string> = {
  merchant: 'Merchant', purchase_date: 'Date', total_milliunits: 'Total', currency: 'Currency', kind: 'Kind',
  reference: 'Reference', items: 'Items', classification: 'Receipt or not',
};
const SIGNAL_LABELS: Record<string, string> = {
  amount: 'amount', partial_amount: 'items fit amount', date: 'date', merchant: 'merchant', card: 'card digits',
  reference: 'order reference',
};
const small: React.CSSProperties = { fontSize: '0.78rem', padding: '3px 10px' };

interface Draft {
  merchant: string; purchase_date: string; total: string; currency: string; kind: string; reference: string;
  items: { description: string; quantity: string; amount: string; category_id?: string | null; category_source?: string }[];
}

function draftOf(r: ReceiptDetail): Draft {
  return {
    merchant: r.merchant, purchase_date: r.purchase_date ?? '', currency: r.currency, kind: r.kind, reference: r.reference,
    total: r.total_milliunits != null ? formatMilliunits(r.total_milliunits) : '',
    items: r.items.map((i) => ({
      description: i.description, quantity: String(i.quantity ?? 1), amount: formatMilliunits(i.amount_milliunits),
      category_id: i.category_source === 'user' || i.category_source === 'ai' ? i.category_id : null,
      category_source: i.category_source === 'user' || i.category_source === 'ai' ? i.category_source : undefined,
    })),
  };
}

/** Changed fields only, validated locally first; returns an error message instead when something is unreadable. */
function changesOf(r: ReceiptDetail, d: Draft): Record<string, unknown> | string {
  const out: Record<string, unknown> = {};
  if (d.merchant.trim() !== r.merchant) out.merchant = d.merchant;
  if ((d.purchase_date || null) !== r.purchase_date) out.purchase_date = d.purchase_date || null;
  if (d.currency.trim().toUpperCase() !== r.currency) out.currency = d.currency;
  if (d.kind !== r.kind) out.kind = d.kind;
  if (d.reference.trim() !== r.reference) out.reference = d.reference;
  const total = d.total.trim() ? parseMilliunits(d.total) : null;
  if (d.total.trim() && total === null) return 'The total is not a valid amount.';
  if (total !== r.total_milliunits) out.total_milliunits = total;
  const items = [];
  for (const [n, i] of d.items.entries()) {
    const amount = parseMilliunits(i.amount);
    const quantity = Number(i.quantity);
    if (amount === null) return `The amount of item ${n + 1} is not valid.`;
    if (!(quantity > 0)) return `The quantity of item ${n + 1} must be a positive number.`;
    items.push({ description: i.description, quantity, amount_milliunits: amount,
      ...(i.category_id ? { category_id: i.category_id, category_source: i.category_source } : {}) });
  }
  const before = r.items.map((i) => [i.description, i.quantity, i.amount_milliunits]);
  const after = items.map((i) => [i.description, i.quantity, i.amount_milliunits]);
  if (JSON.stringify(before) !== JSON.stringify(after)) out.items = items;
  return out;
}


interface Props {
  receiptId: string;
  planId: string;
  categoryGroups: CategoryGroup[];
  onClose: () => void;
  onOpenReceipt: (id: string) => void;
}

/** One receipt: evidence, corrections with reset, classification, exclusion, AI re-read, item categories and links. */
export default function ReceiptDetailPanel({ receiptId, planId, categoryGroups, onClose, onOpenReceipt }: Props) {
  const queryClient = useQueryClient();
  const key = ['receipt', receiptId, planId];
  const detail = useQuery({ queryKey: key, queryFn: () => fetchReceipt(receiptId, planId) });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [estimate, setEstimate] = useState<AIEstimate | null>(null);
  const [candidates, setCandidates] = useState<ReceiptMatchView[] | null>(null);

  const r = detail.data;

  const run = async (action: () => Promise<ReceiptDetail | void>, success?: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const updated = await action();
      if (updated) {
        queryClient.setQueryData(key, updated);
        if (updated.note) setMessage({ text: updated.note, error: true });
        else if (success) setMessage({ text: success });
      }
      queryClient.invalidateQueries({ queryKey: ['receipts'] });
      queryClient.invalidateQueries({ queryKey: ['spending'] });
      queryClient.invalidateQueries({ queryKey: ['activeBatch'] });
    } catch (e) {
      setMessage({ text: (e as Error).message, error: true });
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    if (!r || !draft) return;
    const changes = changesOf(r, draft);
    if (typeof changes === 'string') {
      setMessage({ text: changes, error: true });
      return;
    }
    if (Object.keys(changes).length === 0) {
      setDraft(null);
      return;
    }
    run(async () => {
      const updated = await correctReceipt(receiptId, { fields: changes }, planId);
      setDraft(null);
      return updated;
    }, 'Saved. Your values are kept over any later reading.');
  };

  const askEstimate = () => run(async () => { setEstimate(await estimateReread(receiptId)); });
  const reread = () => run(async () => {
    setEstimate(null);
    return rereadReceipt(receiptId, planId);
  }, 'Read again with AI.');
  const findRows = () => run(async () => { setCandidates((await receiptCandidates(receiptId, planId)).candidates); });
  const link = (rowId: string, action: 'link' | 'unlink' | 'reject') => run(async () => {
    const updated = await linkReceiptRow(receiptId, rowId, action, planId);
    if (candidates) setCandidates((await receiptCandidates(receiptId, planId)).candidates);
    return updated;
  });

  return (
    <div className="card" style={{ padding: 16, flex: '1 1 420px', minWidth: 0, fontSize: '0.85rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>{r ? (r.merchant || r.subject || '(no merchant)') : 'Receipt'}</h3>
        <button className="btn btn-ghost" onClick={onClose} title="Close"><X size={16} /></button>
      </div>
      {detail.isLoading && <p style={{ color: 'var(--text-muted)' }}><Loader2 size={14} className="loading-pulse" /> Loading…</p>}
      {detail.isError && <p style={{ color: 'var(--danger)' }}>{(detail.error as Error).message}</p>}
      {r && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Status and actions */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span className="badge badge-info">{STATUS_LABELS[r.status] ?? r.status}</span>
            <span className="badge badge-accent">{KIND_LABELS[r.kind] ?? r.kind}</span>
            {r.excluded && <span className="badge badge-warning">excluded from spending</span>}
            {r.possible_duplicates.map((id) => (
              <button key={id} className="badge badge-warning" style={{ border: 'none', cursor: 'pointer' }}
                onClick={() => onOpenReceipt(id)} title="Open the other receipt">possible duplicate ›</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {r.status === 'not_receipt' || r.error ? (
              <button className="btn btn-secondary" style={small} disabled={busy}
                onClick={() => run(() => classifyReceipt(receiptId, 'receipt', planId), 'Read again from the mailbox.')}>
                <CheckCircle size={12} /> This is a receipt
              </button>
            ) : (
              <button className="btn btn-ghost" style={small} disabled={busy}
                onClick={() => run(() => classifyReceipt(receiptId, 'not_receipt', planId))}>
                <Ban size={12} /> Not a receipt
              </button>
            )}
            <button className="btn btn-ghost" style={small} disabled={busy}
              onClick={() => run(() => correctReceipt(receiptId, { excluded: !r.excluded }, planId))}>
              {r.excluded ? <><Eye size={12} /> Count in spending</> : <><EyeOff size={12} /> Exclude from spending</>}
            </button>
            {r.status !== 'not_receipt' && (
              <button className="btn btn-ghost" style={small} disabled={busy} onClick={askEstimate}>
                <Sparkles size={12} /> Read again with AI
              </button>
            )}
            {!draft && r.status !== 'not_receipt' && (
              <button className="btn btn-ghost" style={small} disabled={busy} onClick={() => setDraft(draftOf(r))}>
                <Pencil size={12} /> Correct
              </button>
            )}
          </div>
          {estimate && (
            <div style={{ background: 'var(--bg-surface)', borderRadius: 'var(--radius-sm)', padding: 8 }}>
              One AI request, about ${estimate.estimate_usd.toFixed(3)} (${estimate.remaining_usd.toFixed(2)} of
              ${estimate.budget_usd.toFixed(2)} left this month). Corrected fields stay as they are.
              <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                <button className="btn btn-primary" style={small} disabled={busy} onClick={reread}>Read again</button>
                <button className="btn btn-ghost" style={small} onClick={() => setEstimate(null)}>Cancel</button>
              </div>
            </div>
          )}
          {message && <div style={{ color: message.error ? 'var(--danger)' : 'var(--success)' }}>{message.text}</div>}
          {r.error && !message && <div style={{ color: 'var(--danger)' }}>{r.error}</div>}

          {r.status === 'needs_check' && (
            <div style={{ background: 'var(--warning-bg)', color: 'var(--warning)', padding: 8, borderRadius: 'var(--radius-sm)' }}>
              Totals disagree: AI read {r.total_milliunits != null ? `${r.currency} ${formatMilliunits(r.total_milliunits)}` : '—'},
              the email or PDF says {r.local_total_milliunits != null ? `${r.currency} ${formatMilliunits(r.local_total_milliunits)}` : '—'}.
              Correct the total if needed.
            </div>
          )}

          {/* Fields */}
          {draft ? (
            <FieldsForm draft={draft} setDraft={setDraft} busy={busy} onSave={save} onCancel={() => setDraft(null)} />
          ) : (
            <Fields receipt={r} busy={busy}
              onReset={(field) => run(() => correctReceipt(receiptId, { reset: [field] }, planId), 'Back to the extracted value.')} />
          )}

          {/* Items */}
          {!draft && r.items.length > 0 && (
            <div>
              <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>Items</div>
              <table className="results-table">
                <colgroup><col /><col style={{ width: 80 }} /><col style={{ width: 190 }} /></colgroup>
                <tbody>
                  {r.items.map((item) => (
                    <tr key={item.index}>
                      <td className="no-truncate">{item.quantity !== 1 ? `${item.quantity} × ` : ''}{item.description}</td>
                      <td style={{ textAlign: 'right' }}>{formatMilliunits(item.amount_milliunits)}</td>
                      <td className="no-truncate">
                        <CategoryPicker value={item.category_name ?? ''} groups={categoryGroups} autoFocus={false}
                          inputStyle={{ ...inputStyle, width: '100%', padding: '3px 6px' }}
                          onChange={(name, id) => {
                            const categoryId = id ?? null;
                            if (name && !categoryId) return;
                            run(() => setItemCategory(receiptId, item.index, categoryId, planId));
                          }} />
                        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                          {SOURCE_LABELS[item.category_source] ?? item.category_source}
                          {item.category_source === 'user' && (
                            <button className="btn btn-ghost" style={{ ...small, padding: '0 4px' }} disabled={busy}
                              onClick={() => run(() => setItemCategory(receiptId, item.index, null, planId))}>clear</button>
                          )}
                          {(item.note || item.category_note) && <span style={{ color: 'var(--warning)' }}> · {item.note || item.category_note}</span>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Links */}
          <div>
            <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>
              Bank rows (receipts add evidence only; rows and YNAB are never changed)
            </div>
            {r.matches.filter((m) => m.state !== 'rejected').map((m) => (
              <MatchRow key={m.row.id} match={m} busy={busy} onAction={(a) => link(m.row.id, a)} />
            ))}
            {r.matches.filter((m) => m.state !== 'rejected').length === 0 && (
              <div style={{ color: 'var(--text-muted)' }}>Not linked to any row.</div>
            )}
            {r.status !== 'not_receipt' && (
              <button className="btn btn-ghost" style={{ ...small, marginTop: 6 }} disabled={busy} onClick={findRows}>
                <Search size={12} /> Find rows (any import, also rows already in YNAB)
              </button>
            )}
            {candidates && (
              <div style={{ marginTop: 6 }}>
                {candidates.filter((c) => c.state !== 'linked' && c.state !== 'proposed').map((c) => (
                  <MatchRow key={c.row.id} match={c} busy={busy} onAction={(a) => link(c.row.id, a)} />
                ))}
                {candidates.filter((c) => c.state !== 'linked' && c.state !== 'proposed').length === 0 && (
                  <div style={{ color: 'var(--text-muted)' }}>No other rows within 30 days look like this purchase.</div>
                )}
              </div>
            )}
          </div>

          {/* Evidence */}
          <details>
            <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>Email evidence</summary>
            <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '2px 10px', marginTop: 6 }}>
              <span style={{ color: 'var(--text-muted)' }}>Subject</span><span>{r.subject || '—'}</span>
              <span style={{ color: 'var(--text-muted)' }}>Sender</span><span>{r.sender || '—'}</span>
              <span style={{ color: 'var(--text-muted)' }}>Received</span><span>{r.message_date || '—'}</span>
              <span style={{ color: 'var(--text-muted)' }}>Mailboxes</span>
              <span>{r.sources.map((s) => `${s.mailbox} / ${s.folder}`).join(', ') || '—'}</span>
              <span style={{ color: 'var(--text-muted)' }}>Read from</span>
              <span>{{ text: 'email text', pdf_text: 'PDF text', ai_document: 'AI document reading' }[r.extraction_method] ?? (r.extraction_method || '—')}</span>
            </div>
            {r.text_excerpt && (
              <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflowY: 'auto', background: 'var(--bg-surface)',
                padding: 8, borderRadius: 'var(--radius-sm)', fontSize: '0.75rem', marginTop: 6 }}>{r.text_excerpt}</pre>
            )}
          </details>
        </div>
      )}
    </div>
  );
}

function Fields({ receipt: r, busy, onReset }: { receipt: ReceiptDetail; busy: boolean; onReset: (field: string) => void }) {
  const rows: [string, string][] = [
    ['merchant', r.merchant || '—'],
    ['purchase_date', r.purchase_date || '—'],
    ['total_milliunits', r.total_milliunits != null ? `${r.currency} ${formatMilliunits(r.total_milliunits)}` : '—'],
    ['reference', r.reference || '—'],
  ];
  const resettable = r.user_fields.filter((f) => !rows.some(([k]) => k === f));
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '2px 10px', alignItems: 'center' }}>
      {rows.map(([field, value]) => (
        <FieldRow key={field} label={FIELD_LABELS[field]} value={value} corrected={r.user_fields.includes(field)}
          busy={busy} onReset={() => onReset(field)} />
      ))}
      {resettable.map((field) => (
        <FieldRow key={field} label={FIELD_LABELS[field] ?? field} value="corrected by you" corrected busy={busy}
          onReset={() => onReset(field)} />
      ))}
    </div>
  );
}

function FieldRow({ label, value, corrected, busy, onReset }: {
  label: string; value: string; corrected: boolean; busy: boolean; onReset: () => void;
}) {
  return (
    <>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span>
        {value}
        {corrected && (
          <button className="btn btn-ghost" style={{ ...small, padding: '0 6px', marginLeft: 6 }} disabled={busy}
            onClick={onReset} title="Return to the extracted value">
            <RotateCcw size={11} /> yours · reset
          </button>
        )}
      </span>
    </>
  );
}

function FieldsForm({ draft, setDraft, busy, onSave, onCancel }: {
  draft: Draft; setDraft: (d: Draft) => void; busy: boolean; onSave: () => void; onCancel: () => void;
}) {
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const setItem = (index: number, patch: Partial<Draft['items'][number]>) =>
    set({ items: draft.items.map((i, n) => (n === index ? { ...i, ...patch } : i)) });
  const field = (label: string, node: React.ReactNode) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: '0.78rem', color: 'var(--text-muted)' }}>
      {label}{node}
    </label>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
        {field('Merchant', <input style={inputStyle} value={draft.merchant} onChange={(e) => set({ merchant: e.target.value })} />)}
        {field('Date', <input type="date" style={inputStyle} value={draft.purchase_date} onChange={(e) => set({ purchase_date: e.target.value })} />)}
        {field('Total', <input style={inputStyle} value={draft.total} placeholder="e.g. 51.00" onChange={(e) => set({ total: e.target.value })} />)}
        {field('Currency', <input style={inputStyle} value={draft.currency} maxLength={3} onChange={(e) => set({ currency: e.target.value })} />)}
        {field('Kind', (
          <select style={inputStyle} value={draft.kind} onChange={(e) => set({ kind: e.target.value })}>
            {Object.entries(KIND_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        ))}
        {field('Reference', <input style={inputStyle} value={draft.reference} onChange={(e) => set({ reference: e.target.value })} />)}
      </div>
      <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>Items (amounts without sign, in the receipt currency)</div>
      {draft.items.map((item, n) => (
        <div key={n} style={{ display: 'flex', gap: 6 }}>
          <input style={{ ...inputStyle, flex: 1 }} value={item.description} placeholder="Description"
            onChange={(e) => setItem(n, { description: e.target.value })} />
          <input style={{ ...inputStyle, width: 50 }} value={item.quantity} title="Quantity"
            onChange={(e) => setItem(n, { quantity: e.target.value })} />
          <input style={{ ...inputStyle, width: 90 }} value={item.amount} placeholder="0.00"
            onChange={(e) => setItem(n, { amount: e.target.value })} />
          <button className="btn btn-ghost" style={small} onClick={() => set({ items: draft.items.filter((_, i) => i !== n) })}
            title="Remove item"><Trash2 size={12} /></button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 6 }}>
        <button className="btn btn-ghost" style={small}
          onClick={() => set({ items: [...draft.items, { description: '', quantity: '1', amount: '' }] })}>
          <Plus size={12} /> Add item
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn btn-primary" style={small} disabled={busy} onClick={onSave}>Save</button>
        <button className="btn btn-ghost" style={small} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function MatchRow({ match: m, busy, onAction }: {
  match: ReceiptMatchView; busy: boolean; onAction: (a: 'link' | 'unlink' | 'reject') => void;
}) {
  const row = m.row;
  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: '6px 0', display: 'flex', gap: 8, alignItems: 'center' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div>
          {row.date} · {row.amount_milliunits != null ? `${row.amount_milliunits < 0 ? '-' : ''}${formatMilliunits(row.amount_milliunits)}` : '—'}
          {' '}· {row.bank_text}
        </div>
        <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
          {m.state === 'linked' ? (m.origin === 'auto' ? 'linked automatically' : 'linked by you') : `match ${(m.score * 100).toFixed(0)}%`}
          {Object.keys(m.signals).length > 0 && ` (${Object.keys(m.signals).map((k) => SIGNAL_LABELS[k] ?? k).join(', ')})`}
          {row.in_ynab ? ' · already in YNAB' : ''}{row.category ? ` · ${row.category}` : ''}{row.split ? ' · split' : ''}
        </div>
      </div>
      {m.state === 'linked' ? (
        <button className="btn btn-ghost" style={small} disabled={busy} onClick={() => onAction('unlink')}><Unlink size={12} /> Unlink</button>
      ) : (
        <>
          <button className="btn btn-secondary" style={small} disabled={busy} onClick={() => onAction('link')}><Link2 size={12} /> Link</button>
          {m.state === 'proposed' && (
            <button className="btn btn-ghost" style={{ ...small, color: 'var(--danger)' }} disabled={busy}
              onClick={() => onAction('reject')} title="Not this purchase"><X size={12} /></button>
          )}
        </>
      )}
    </div>
  );
}


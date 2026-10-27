import { useCallback, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Receipt, Link2, Unlink, X, ExternalLink } from 'lucide-react';
import type { ImportRowData, ReceiptView } from '../api/client';
import { decideReceipt } from '../api/client';
import { formatMilliunits } from '../lib/splits';

const SIGNAL_LABELS: Record<string, string> = {
  amount: 'amount', partial_amount: 'items fit amount', date: 'date', merchant: 'merchant', card: 'card digits',
  reference: 'order reference',
};

const KIND_LABELS: Record<string, string> = {
  purchase: 'Purchase', refund: 'Refund', shipping: 'Shipping notice', payment: 'Payment notice',
};

interface Props {
  row: ImportRowData;
  onRowReplaced: (row: ImportRowData) => void;
  onOpenReceipt?: (receiptId: string) => void;
}

/** Receipt evidence for a row: a badge, and a popover with details and Link / Unlink / Reject. */
export default function ReceiptBadge({ row, onRowReplaced, onOpenReceipt }: Props) {
  const receipts = row.receipts ?? [];
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const toggle = useCallback(() => {
    if (!open && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 4, left: Math.max(8, Math.min(rect.left, window.innerWidth - 440)) });
    }
    setOpen(!open);
  }, [open]);

  if (receipts.length === 0) return null;
  const linked = receipts.some((r) => r.state === 'linked' && r.role === 'primary');

  const act = async (receipt: ReceiptView, action: 'link' | 'unlink' | 'reject') => {
    setBusy(true);
    setError(null);
    try {
      onRowReplaced(await decideReceipt(row.id, receipt.receipt_id, action));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        ref={btnRef}
        className={`badge ${linked ? 'badge-success' : 'badge-info'}`}
        style={{ border: 'none', cursor: 'pointer', marginLeft: 6 }}
        title={linked ? 'Receipt linked — click for details' : `${receipts.length} possible receipt(s) — click to choose`}
        onClick={(e) => { e.stopPropagation(); toggle(); }}
      >
        <Receipt size={12} /> {linked ? 'Receipt' : `${receipts.length}?`}
      </button>
      {open && createPortal(
        <>
          {/* Portal events still bubble through React: keep them away from the row's cell editors */}
          <div style={{ position: 'fixed', inset: 0, zIndex: 9998 }} onMouseDown={() => setOpen(false)}
            onClick={(e) => e.stopPropagation()} />
          <div onClick={(e) => e.stopPropagation()} style={{
            position: 'fixed', top: pos.top, left: pos.left, zIndex: 9999, width: 430, maxHeight: '60vh',
            overflowY: 'auto', background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)', padding: 12, boxShadow: 'var(--shadow-glow)', fontSize: '0.82rem',
          }}>
            <div style={{ color: 'var(--text-muted)', marginBottom: 8 }}>
              Receipts add evidence only; the bank amount and date never change.
            </div>
            {receipts.map((r) => (
              <div key={r.receipt_id} style={{ borderTop: '1px solid var(--border)', padding: '8px 0' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                  <strong>{r.merchant || 'Unknown merchant'}</strong>
                  <span>{r.total_milliunits != null ? `${r.currency} ${formatMilliunits(r.total_milliunits)}` : ''}</span>
                </div>
                <div style={{ color: 'var(--text-secondary)' }}>
                  {KIND_LABELS[r.kind] ?? r.kind}{r.purchase_date ? ` · ${r.purchase_date}` : ''}
                  {r.reference ? ` · ${r.reference}` : ''}{r.mailboxes.length ? ` · from ${r.mailboxes.join(', ')}` : ''}
                  {r.status === 'needs_check' && <span style={{ color: 'var(--warning)' }}> · totals disagree, check it</span>}
                </div>
                {r.items.length > 0 && (
                  <ul style={{ margin: '4px 0', paddingLeft: 18, color: 'var(--text-secondary)' }}>
                    {r.items.slice(0, 8).map((item, i) => (
                      <li key={i}>{item.description} — {formatMilliunits(item.amount_milliunits)}</li>
                    ))}
                    {r.items.length > 8 && <li>… {r.items.length - 8} more</li>}
                  </ul>
                )}
                <div style={{ color: 'var(--text-muted)' }}>
                  {r.role === 'supporting' ? 'Supports the linked purchase' : `Match ${(r.score * 100).toFixed(0)}%: `}
                  {r.role === 'primary' && Object.keys(r.signals).map((k) => SIGNAL_LABELS[k] ?? k).join(', ')}
                  {r.state === 'linked' ? (r.origin === 'auto' ? ' · linked automatically' : ' · linked by you') : ''}
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                  {r.state !== 'linked' && (
                    <button className="btn btn-secondary" style={{ fontSize: '0.78rem', padding: '3px 10px' }}
                      disabled={busy} onClick={() => act(r, 'link')}>
                      <Link2 size={12} /> Link
                    </button>
                  )}
                  {r.state === 'linked' && (
                    <button className="btn btn-ghost" style={{ fontSize: '0.78rem', padding: '3px 10px' }}
                      disabled={busy} onClick={() => act(r, 'unlink')}>
                      <Unlink size={12} /> Unlink
                    </button>
                  )}
                  <button className="btn btn-ghost" style={{ fontSize: '0.78rem', padding: '3px 10px', color: 'var(--danger)' }}
                    disabled={busy} onClick={() => act(r, 'reject')}>
                    <X size={12} /> Not this purchase
                  </button>
                  {onOpenReceipt && (
                    <button className="btn btn-ghost" style={{ fontSize: '0.78rem', padding: '3px 10px' }}
                      onClick={() => { setOpen(false); onOpenReceipt(r.receipt_id); }} title="Open on the Receipts page">
                      <ExternalLink size={12} /> Details
                    </button>
                  )}
                </div>
              </div>
            ))}
            {error && <div style={{ color: 'var(--danger)' }}>{error}</div>}
          </div>
        </>,
        document.body,
      )}
    </>
  );
}

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Receipt, PieChart, Loader2, Copy, Link2, AlertTriangle } from 'lucide-react';
import { listReceipts } from '../api/client';
import type { CategoryGroup, ReceiptFilters, ReceiptSummary } from '../api/client';
import { formatMilliunits } from '../lib/splits';
import { KIND_LABELS, STATUS_LABELS, inputStyle } from '../lib/receipts';
import ReceiptDetailPanel from './ReceiptDetailPanel';
import SpendingReport from './SpendingReport';

const STATUS_BADGE: Record<string, string> = {
  extracted: 'badge-success', needs_check: 'badge-warning', candidate: 'badge-info', not_receipt: 'badge-accent',
  error: 'badge-danger',
};


const PAGE_SIZE = 50;

interface Props {
  planId: string;
  categoryGroups: CategoryGroup[];
  /** Opened on mount; the page is remounted (keyed) to open another one. */
  openReceiptId?: string | null;
}

/** Receipts and skipped messages: filters, a paginated list and a detail panel; and the spending tab. */
export default function ReceiptsPage({ planId, categoryGroups, openReceiptId }: Props) {
  const [tab, setTab] = useState<'receipts' | 'spending'>('receipts');
  const [filters, setFilters] = useState<ReceiptFilters>({});
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(openReceiptId ?? null);

  const list = useQuery({
    queryKey: ['receipts', filters, page],
    queryFn: () => listReceipts({ ...filters, page, page_size: PAGE_SIZE }),
    enabled: tab === 'receipts',
  });

  const setFilter = (key: keyof ReceiptFilters, value: string) => {
    setFilters((f) => ({ ...f, [key]: value || undefined }));
    setPage(1);
  };
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1;

  const openReceipt = (id: string) => {
    setTab('receipts');
    setSelected(id);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className={`btn ${tab === 'receipts' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('receipts')}>
          <Receipt size={16} /> Receipts
        </button>
        <button className={`btn ${tab === 'spending' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setTab('spending')}>
          <PieChart size={16} /> Spending
        </button>
      </div>

      {tab === 'spending' && <SpendingReport planId={planId} onOpenReceipt={openReceipt} />}

      {tab === 'receipts' && (
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div className="card" style={{ padding: 16, flex: '1 1 520px', minWidth: 0 }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              <input style={{ ...inputStyle, flex: '1 1 160px' }} placeholder="Search merchant, subject, reference…"
                value={filters.q ?? ''} onChange={(e) => setFilter('q', e.target.value)} />
              <select style={inputStyle} value={filters.status ?? ''} onChange={(e) => setFilter('status', e.target.value)}>
                <option value="">Any status</option>
                {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <select style={inputStyle} value={filters.kind ?? ''} onChange={(e) => setFilter('kind', e.target.value)}>
                <option value="">Any kind</option>
                {Object.entries(KIND_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <select style={inputStyle} value={filters.linked ?? ''} onChange={(e) => setFilter('linked', e.target.value)}>
                <option value="">Linked or not</option>
                <option value="true">Linked</option>
                <option value="false">Not linked</option>
              </select>
              <input style={{ ...inputStyle, width: 90 }} placeholder="Mailbox" value={filters.mailbox ?? ''}
                onChange={(e) => setFilter('mailbox', e.target.value)} />
              <input type="date" style={inputStyle} value={filters.from ?? ''} title="From"
                onChange={(e) => setFilter('from', e.target.value)} />
              <input type="date" style={inputStyle} value={filters.to ?? ''} title="To"
                onChange={(e) => setFilter('to', e.target.value)} />
            </div>
            {list.isLoading && <p style={{ color: 'var(--text-muted)' }}><Loader2 size={14} className="loading-pulse" /> Loading…</p>}
            {list.isError && <p style={{ color: 'var(--danger)' }}>{(list.error as Error).message}</p>}
            {list.data && (
              <>
                <table className="results-table">
                  <colgroup>
                    <col style={{ width: 104 }} /><col /><col style={{ width: 110 }} /><col style={{ width: 120 }} />
                    <col style={{ width: 120 }} /><col style={{ width: 70 }} />
                  </colgroup>
                  <thead>
                    <tr><th>Date</th><th>Merchant</th><th style={{ textAlign: 'right' }}>Total</th><th>Kind</th><th>Status</th><th>Rows</th></tr>
                  </thead>
                  <tbody>
                    {list.data.receipts.map((r) => (
                      <ReceiptListRow key={r.id} receipt={r} selected={r.id === selected} onSelect={() => setSelected(r.id)} />
                    ))}
                  </tbody>
                </table>
                {list.data.receipts.length === 0 && (
                  <p style={{ color: 'var(--text-muted)', padding: 12 }}>No receipts match these filters.</p>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10,
                  fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                  <span>{list.data.total} message(s)</span>
                  <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <button className="btn btn-ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</button>
                    page {page} / {pages}
                    <button className="btn btn-ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>›</button>
                  </span>
                </div>
              </>
            )}
          </div>
          {selected && (
            <ReceiptDetailPanel receiptId={selected} planId={planId} categoryGroups={categoryGroups}
              onClose={() => setSelected(null)} onOpenReceipt={setSelected} />
          )}
        </div>
      )}
    </div>
  );
}

function ReceiptListRow({ receipt: r, selected, onSelect }: { receipt: ReceiptSummary; selected: boolean; onSelect: () => void }) {
  return (
    <tr onClick={onSelect} style={{ cursor: 'pointer', background: selected ? 'var(--accent-subtle)' : undefined }}>
      <td>{r.date ?? '—'}</td>
      <td title={r.subject}>
        <span style={{ color: 'var(--text-primary)' }}>{r.merchant || r.subject || '(no merchant)'}</span>
        {r.possible_duplicates.length > 0 && (
          <span className="badge badge-warning" style={{ marginLeft: 6 }} title="Same merchant, total and date as another receipt">
            <Copy size={10} /> duplicate?
          </span>
        )}
        {r.excluded && <span className="badge badge-accent" style={{ marginLeft: 6 }}>excluded</span>}
        {r.user_fields.length > 0 && <span className="badge badge-info" style={{ marginLeft: 6 }} title={`Corrected: ${r.user_fields.join(', ')}`}>edited</span>}
        {r.error && <AlertTriangle size={12} color="var(--danger)" style={{ marginLeft: 6 }} />}
      </td>
      <td style={{ textAlign: 'right' }}>
        {r.total_milliunits != null ? `${r.currency} ${formatMilliunits(r.total_milliunits)}` : '—'}
      </td>
      <td>{KIND_LABELS[r.kind] ?? r.kind}</td>
      <td><span className={`badge ${STATUS_BADGE[r.status] ?? 'badge-info'}`}>{STATUS_LABELS[r.status] ?? r.status}</span></td>
      <td>{r.linked_rows > 0 ? <><Link2 size={12} /> {r.linked_rows}</> : '—'}</td>
    </tr>
  );
}

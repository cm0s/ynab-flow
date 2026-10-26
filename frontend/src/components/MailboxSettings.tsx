import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Mail, Loader2, AlertTriangle } from 'lucide-react';
import { fetchReceiptStatus, runReceiptFetch } from '../api/client';

const RECEIPT_LABELS: Record<string, string> = {
  extracted: 'read', needs_check: 'need a check', candidate: 'waiting for AI', not_receipt: 'not receipts', error: 'failed',
};

/** Proton Mail Bridge mailboxes: configuration state, last fetch per folder, and a read-only "Fetch receipts". */
export default function MailboxSettings({ onFetched }: { onFetched?: () => void }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ['receiptStatus'], queryFn: fetchReceiptStatus });
  const fetchMutation = useMutation({
    mutationFn: runReceiptFetch,
    onSuccess: () => onFetched?.(),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['receiptStatus'] });
      queryClient.invalidateQueries({ queryKey: ['activeBatch'] });
    },
  });
  const data = status.data;
  const counts = Object.entries(data?.receipts ?? {});

  return (
    <div className="card" style={{ padding: 24 }}>
      <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Mail size={18} color="var(--accent)" /> Receipts from Proton Mail
      </h3>
      <p style={{ marginBottom: 12, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
        Reads receipts from Proton Mail Bridge on this computer. Access is read-only: messages are never
        moved, deleted or marked as read. Mailboxes are configured in <code>backend/.env</code>.
      </p>
      {status.isError && (
        <p style={{ color: 'var(--danger)', fontSize: '0.85rem' }}>{(status.error as Error).message}</p>
      )}
      {data && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: 12 }}>{data.feature.reason}</p>
      )}
      {data && data.mailboxes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
          {data.mailboxes.map((m) => m.folders.map((f) => (
            <div key={`${m.mailbox}/${f.folder}`} style={{ fontSize: '0.85rem', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <strong>{m.mailbox}</strong>
              <span style={{ color: 'var(--text-muted)' }}>{f.folder}</span>
              <span style={{ color: 'var(--text-secondary)' }}>
                {f.last_fetch_at ? `last fetched ${new Date(f.last_fetch_at + 'Z').toLocaleString()}` : 'never fetched'}
              </span>
              {f.error && (
                <span style={{ color: 'var(--danger)', display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                  <AlertTriangle size={12} /> {f.error}
                </span>
              )}
            </div>
          )))}
        </div>
      )}
      {counts.length > 0 && (
        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 12 }}>
          Receipts: {counts.map(([k, v]) => `${v} ${RECEIPT_LABELS[k] ?? k}`).join(' · ')}
        </p>
      )}
      <button
        className="btn btn-primary"
        onClick={() => fetchMutation.mutate()}
        disabled={!data?.feature.enabled || fetchMutation.isPending || data?.running}
      >
        {fetchMutation.isPending ? <><Loader2 size={16} className="loading-pulse" /> Fetching…</> : <><Mail size={16} /> Fetch receipts</>}
      </button>
      {fetchMutation.isSuccess && (
        <p style={{ marginTop: 8, color: 'var(--success)', fontSize: '0.85rem' }}>
          ✓ {fetchMutation.data.messages} new message(s): {fetchMutation.data.new_receipts} receipt(s),
          {' '}{fetchMutation.data.duplicates} already known, {fetchMutation.data.not_receipts} not receipts.
          {fetchMutation.data.ai_note && <> {fetchMutation.data.ai_note}</>}
        </p>
      )}
      {fetchMutation.isError && (
        <p style={{ marginTop: 8, color: 'var(--danger)', fontSize: '0.85rem' }}>{(fetchMutation.error as Error).message}</p>
      )}
    </div>
  );
}

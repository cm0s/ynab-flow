import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, RefreshCw, Loader2 } from 'lucide-react';
import { getCandidates } from '../api/client';
import type { Account, ResolveAction, LinkCandidate } from '../api/client';
import type { ReviewedRow } from './ReviewTable';
import { needsResolution } from '../lib/importFlow';

interface Props {
  rows: ReviewedRow[];
  accounts: Account[];
  cardAccountId: string | null;
  suggestedAccountIds: string[];
  busy: boolean;
  warning: string | null;
  onRecheck: () => void;
  onResolve: (rowId: string, action: ResolveAction, ynabTransactionId?: string) => void;
  onRoute: (rowId: string, accountId: string | null) => void;
}

const absAmount = (r: ReviewedRow) =>
  (Math.abs(r.amount_milliunits ?? Math.round(r.amount * 1000)) / 1000).toFixed(2);

const REASON: Record<string, string> = {
  potential: 'A YNAB transaction has the same account, date and amount, but it is not certain it is the same one.',
  conflict: 'Several YNAB transactions could match.',
  deleted_link: 'The YNAB transaction this was linked to has been deleted.',
  unverified: 'YNAB history could not be checked, so duplicates cannot be ruled out.',
  ambiguous: 'This purchase appeared a different number of times in an earlier file.',
  legacy_unresolved: 'Imported before account selection existed. Confirm the account first.',
  quarantined: 'The stored amount cannot be represented exactly; re-import this file.',
};

const describe = (c: LinkCandidate) =>
  [c.payee || 'No payee', c.category, c.memo].filter(Boolean).join(' · ');

/** Pick one of the YNAB transactions that share this row's account, date and amount. */
function LinkExisting({ rowId, busy, onLink }: { rowId: string; busy: boolean; onLink: (id: string) => void }) {
  const [choice, setChoice] = useState('');
  const { data, isLoading } = useQuery({ queryKey: ['row-candidates', rowId], queryFn: () => getCandidates(rowId) });
  const candidates = data?.candidates ?? [];
  if (!isLoading && candidates.length === 0) {
    return <span style={{ color: 'var(--text-muted)' }}>No linkable YNAB transaction found.</span>;
  }
  return (
    <>
      <div className="select-wrapper">
        <select aria-label="Existing YNAB transaction" value={choice} disabled={isLoading}
          onChange={(e) => setChoice(e.target.value)}>
          <option value="">{isLoading ? 'Loading…' : 'Link to existing…'}</option>
          {candidates.map((c) => (
            <option key={c.ynab_transaction_id} value={c.ynab_transaction_id}>{describe(c)}</option>
          ))}
        </select>
      </div>
      <button className="btn btn-secondary" disabled={busy || !choice} onClick={() => onLink(choice)}>Link existing</button>
    </>
  );
}

/** Rows that cannot be written yet, with the actions that resolve them. Bank values are never editable here. */
export default function ResolutionPanel({
  rows, accounts, cardAccountId, suggestedAccountIds, busy, warning, onRecheck, onResolve, onRoute,
}: Props) {
  const [picked, setPicked] = useState<Record<string, string>>({});
  const blocked = rows.filter(needsResolution);
  if (blocked.length === 0 && !warning) return null;

  const options = accounts
    .filter((a) => a.id !== cardAccountId)
    .sort((a, b) => Number(suggestedAccountIds.includes(b.id)) - Number(suggestedAccountIds.includes(a.id)));

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16, borderColor: 'var(--warning)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <strong style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertTriangle size={16} style={{ color: 'var(--warning)' }} />
          {blocked.length} row{blocked.length === 1 ? ' needs' : 's need'} attention before {blocked.length === 1 ? 'it' : 'they'} can be pushed
        </strong>
        <button className="btn btn-secondary" onClick={onRecheck} disabled={busy}>
          {busy ? <Loader2 size={14} className="loading-pulse" /> : <RefreshCw size={14} />} Check against YNAB
        </button>
      </div>
      {warning && <p style={{ color: 'var(--danger)', fontSize: '0.85rem', marginBottom: 8 }}>{warning}</p>}
      <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginBottom: 8 }}>
        Repayments are transfers: no card owner or lunch policy is assumed, so choose the account the money came from,
        or keep the row for later. Bank amounts and dates cannot be changed here.
      </p>
      {blocked.map((r) => {
        const isRepayment = r.transfer_state === 'candidate' || r.transfer_state === 'ambiguous';
        const state = r.reconciliation_state ?? '';
        return (
          <div key={r.id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0', fontSize: '0.85rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <span><strong>{r.date}</strong> · {r.original_memo}</span>
              <span className={`amount ${r.amount >= 0 ? 'positive' : 'negative'}`}>
                {r.amount >= 0 ? '+' : '-'}{absAmount(r)} {r.currency || ''}
              </span>
            </div>
            <div style={{ color: 'var(--text-muted)', margin: '4px 0' }}>
              {isRepayment ? 'Card repayment (transfer).' : REASON[state] ?? 'Needs review.'} {r.reconciliation_note}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              {isRepayment && (
                <>
                  <div className="select-wrapper"><select
                    aria-label="Paid from account"
                    value={picked[r.id] ?? r.transfer_account_id ?? ''}
                    onChange={(e) => setPicked({ ...picked, [r.id]: e.target.value })}
                  >
                    <option value="">Paid from…</option>
                    {options.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}{suggestedAccountIds.includes(a.id) ? ' (previous transfers)' : ''}
                      </option>
                    ))}
                  </select></div>
                  <button
                    className="btn btn-primary"
                    disabled={busy || !(picked[r.id] ?? r.transfer_account_id)}
                    onClick={() => onRoute(r.id, picked[r.id] ?? r.transfer_account_id ?? null)}
                  >Use this account</button>
                  <button className="btn btn-ghost" disabled={busy} onClick={() => onRoute(r.id, null)}>Keep for later</button>
                </>
              )}
              {!isRepayment && (state === 'potential' || state === 'conflict') && (
                <>
                  <button className="btn btn-primary" disabled={busy} onClick={() => onResolve(r.id, 'confirm_new')}>New purchase</button>
                  <LinkExisting rowId={r.id} busy={busy} onLink={(id) => onResolve(r.id, 'link_existing', id)} />
                </>
              )}
              {!isRepayment && state === 'deleted_link' && (
                <button className="btn btn-primary" disabled={busy} onClick={() => onResolve(r.id, 'confirm_new')}>Recreate in YNAB</button>
              )}
              {!isRepayment && state === 'ambiguous' && (
                <>
                  <button className="btn btn-secondary" disabled={busy} onClick={() => onResolve(r.id, 'confirm_same')}>Same purchase as before</button>
                  <button className="btn btn-primary" disabled={busy} onClick={() => onResolve(r.id, 'confirm_new')}>New purchase</button>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

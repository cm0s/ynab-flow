import { useMemo, useState } from 'react';
import type { CategoryGroup, ImportRowData } from '../api/client';
import { saveSplits, clearSplits } from '../api/client';
import { CategoryPicker } from './SearchablePicker';
import {
  draftsFromLines, formatMilliunits, linesFromDrafts, unassignedMilliunits,
  type SplitDraft, type SplitLineData,
} from '../lib/splits';
import { Plus, Trash2, Save, Eraser } from 'lucide-react';

export interface SplitSuggestion {
  label: string;           // e.g. "Use receipt split"
  lines: SplitLineData[];
}

interface Props {
  row: ImportRowData;
  categoryGroups: CategoryGroup[];
  suggestions?: SplitSuggestion[];
  onSaved: (row: ImportRowData) => void;
}

const emptyDraft = (): SplitDraft => ({ amount: '', categoryId: '', memo: '', origin: 'user' });

const inputStyle: React.CSSProperties = {
  padding: '4px 8px', background: 'var(--bg-surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.85rem',
};

/** Edit the category lines of one row; amounts are exact and must add up to the bank amount. */
export default function SplitEditor({ row, categoryGroups, suggestions = [], onSaved }: Props) {
  const bank = row.amount_milliunits ?? 0;
  const [drafts, setDrafts] = useState<SplitDraft[]>(() =>
    row.splits && row.splits.length > 0 ? draftsFromLines(row.splits) : [emptyDraft(), emptyDraft()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const names = useMemo(() => {
    const map = new Map<string, string>();
    categoryGroups.forEach((g) => g.categories.forEach((c) => map.set(c.id, c.name)));
    return map;
  }, [categoryGroups]);

  const gap = unassignedMilliunits(bank, drafts);
  const update = (i: number, change: Partial<SplitDraft>) =>
    setDrafts((prev) => prev.map((d, j) => (j === i ? { ...d, ...change, origin: 'user' } : d)));

  const run = async (task: () => Promise<ImportRowData>) => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await task());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    const lines = linesFromDrafts(bank, drafts);
    if (typeof lines === 'string') {
      setError(lines);
      return;
    }
    run(() => saveSplits(row.id, lines));
  };

  const clear = () => run(async () => {
    const saved = await clearSplits(row.id);
    setDrafts([emptyDraft(), emptyDraft()]);
    return saved;
  });

  if (row.amount_milliunits == null) {
    return <div style={{ color: 'var(--text-muted)' }}>This row has no exact bank amount, so it cannot be split.</div>;
  }
  if (row.transfer_state && row.transfer_state !== 'none') {
    return <div style={{ color: 'var(--text-muted)' }}>Transfers and card repayments cannot be split.</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '8px 4px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <strong style={{ fontSize: '0.85rem' }}>Split {bank < 0 ? '−' : '+'}{formatMilliunits(bank)} {row.currency}</strong>
        <span
          className={`badge ${gap === 0 ? 'badge-success' : 'badge-warning'}`}
          title="Bank amount not yet assigned to a line"
        >
          {gap === 0 ? 'Fully assigned' : `${formatMilliunits(gap)} ${Math.abs(bank - gap) > Math.abs(bank) ? 'over-assigned' : 'unassigned'}`}
        </span>
        {suggestions.map((s) => (
          <button key={s.label} className="btn btn-ghost" style={{ fontSize: '0.8rem', padding: '4px 10px' }}
            onClick={() => setDrafts(draftsFromLines(s.lines))}>
            {s.label}
          </button>
        ))}
      </div>
      {drafts.map((d, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '110px 220px 1fr auto', gap: 8, alignItems: 'center' }}>
          <input
            aria-label={`Line ${i + 1} amount`}
            inputMode="decimal"
            placeholder="0.00"
            value={d.amount}
            onChange={(e) => update(i, { amount: e.target.value })}
            style={{ ...inputStyle, textAlign: 'right' }}
          />
          <CategoryPicker
            value={names.get(d.categoryId) ?? ''}
            groups={categoryGroups}
            onChange={(_name, id) => update(i, { categoryId: id ?? '' })}
            inputStyle={inputStyle}
            autoFocus={false}
          />
          <input
            aria-label={`Line ${i + 1} memo`}
            placeholder="Memo (optional)"
            value={d.memo}
            onChange={(e) => update(i, { memo: e.target.value })}
            style={inputStyle}
          />
          <button className="btn btn-ghost" style={{ padding: '4px 6px' }} title="Remove line"
            disabled={drafts.length <= 2} onClick={() => setDrafts((prev) => prev.filter((_, j) => j !== i))}>
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-ghost" style={{ fontSize: '0.8rem' }} onClick={() => setDrafts((p) => [...p, emptyDraft()])}>
          <Plus size={14} /> Add line
        </button>
        <button className="btn btn-primary" style={{ fontSize: '0.8rem' }} disabled={busy} onClick={save}>
          <Save size={14} /> Save split
        </button>
        {row.splits && row.splits.length > 0 && (
          <button className="btn btn-ghost" style={{ fontSize: '0.8rem' }} disabled={busy} onClick={clear}>
            <Eraser size={14} /> Clear split
          </button>
        )}
        {error && <span style={{ color: 'var(--danger)', fontSize: '0.85rem' }}>{error}</span>}
      </div>
    </div>
  );
}

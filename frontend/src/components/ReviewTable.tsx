import { useState, useMemo, useCallback } from 'react';
import type { PredictionRow, CategoryGroup } from '../api/client';
import { CategoryPicker } from './SearchablePicker';
import {
  CheckCircle, AlertTriangle, HelpCircle, Zap, Clock, Brain,
  Check, X, Eye, EyeOff, Filter, CheckCheck, Pencil, Search,
  ArrowUp, ArrowDown, ArrowUpDown,
} from 'lucide-react';

/* ---- Review state per row ---- */
export type ReviewStatus = 'pending' | 'accepted' | 'ignored';

export interface ReviewedRow extends PredictionRow {
  status: ReviewStatus;
  editedPayee: string;
  editedCategory: string;
}

export function toReviewedRows(predictions: PredictionRow[]): ReviewedRow[] {
  return predictions.map((p) => ({
    ...p,
    status: p.review_required ? 'pending' : 'accepted',
    editedPayee: p.payee || '',
    editedCategory: p.category || '',
  }));
}

/* ---- Filter options ---- */
type FilterKey = 'all' | 'pending' | 'accepted' | 'ignored';

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

/* ---- Props ---- */
interface Props {
  rows: ReviewedRow[];
  categoryGroups: CategoryGroup[];
  onUpdateRow: (index: number, update: Partial<ReviewedRow>) => void;
  onBulkAction: (indices: number[], status: ReviewStatus) => void;
  onCreateRule?: (row: ReviewedRow) => void;
}

type SortKey = 'date' | 'memo' | 'amount' | 'payee' | 'category' | 'source_category' | 'source' | 'confidence' | 'status';
type SortDir = 'asc' | 'desc';

export default function ReviewTable({ rows, categoryGroups, onUpdateRow, onBulkAction, onCreateRule }: Props) {
  const [filter, setFilter] = useState<FilterKey>('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editingRow, setEditingRow] = useState<number | null>(null);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const toggleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  }, [sortKey]);

  /* ---- Filtering + Sorting ---- */
  const filtered = useMemo(() => {
    const result = rows
      .map((r, i) => ({ row: r, originalIndex: i }))
      .filter(({ row }) => {
        if (filter !== 'all' && row.status !== filter) return false;
        if (search) {
          const q = search.toLowerCase();
          return (
            row.original_memo.toLowerCase().includes(q) ||
            row.merchant_stem.toLowerCase().includes(q) ||
            (row.editedPayee || '').toLowerCase().includes(q) ||
            (row.editedCategory || '').toLowerCase().includes(q) ||
            row.source_category.toLowerCase().includes(q)
          );
        }
        return true;
      });

    if (sortKey) {
      const dir = sortDir === 'asc' ? 1 : -1;
      result.sort((a, b) => {
        let va: string | number, vb: string | number;
        switch (sortKey) {
          case 'date': va = a.row.date; vb = b.row.date; break;
          case 'memo': va = (a.row.merchant_stem || a.row.cleaned_memo).toLowerCase(); vb = (b.row.merchant_stem || b.row.cleaned_memo).toLowerCase(); break;
          case 'amount': va = a.row.amount; vb = b.row.amount; break;
          case 'payee': va = (a.row.editedPayee || '').toLowerCase(); vb = (b.row.editedPayee || '').toLowerCase(); break;
          case 'category': va = (a.row.editedCategory || '').toLowerCase(); vb = (b.row.editedCategory || '').toLowerCase(); break;
          case 'source_category': va = (a.row.source_category || '').toLowerCase(); vb = (b.row.source_category || '').toLowerCase(); break;
          case 'source': va = a.row.source; vb = b.row.source; break;
          case 'confidence': va = a.row.confidence; vb = b.row.confidence; break;
          case 'status': va = a.row.status; vb = b.row.status; break;
          default: return 0;
        }
        if (va < vb) return -1 * dir;
        if (va > vb) return 1 * dir;
        return 0;
      });
    }

    return result;
  }, [rows, filter, search, sortKey, sortDir]);

  /* ---- Selection ---- */
  const toggleSelect = (idx: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  };

  const selectAll = () => {
    if (selected.size === filtered.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filtered.map((f) => f.originalIndex)));
    }
  };

  const pendingCount = rows.filter((r) => r.status === 'pending').length;
  const acceptedCount = rows.filter((r) => r.status === 'accepted').length;
  const ignoredCount = rows.filter((r) => r.status === 'ignored').length;

  if (rows.length === 0) return null;

  return (
    <div>
      {/* ---- Toolbar ---- */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12,
        marginBottom: 16,
      }}>
        {/* Filter buttons */}
        <div style={{ display: 'flex', gap: 4 }}>
          <button
            className={`btn btn-ghost ${filter === 'all' ? 'btn-secondary' : ''}`}
            onClick={() => setFilter('all')}
            style={{ fontSize: '0.8rem', padding: '6px 12px' }}
          >
            <Filter size={14} /> All ({rows.length})
          </button>
          <button
            className={`btn btn-ghost ${filter === 'pending' ? 'btn-secondary' : ''}`}
            onClick={() => setFilter('pending')}
            style={{ fontSize: '0.8rem', padding: '6px 12px' }}
          >
            <AlertTriangle size={14} /> Pending ({pendingCount})
          </button>
          <button
            className={`btn btn-ghost ${filter === 'accepted' ? 'btn-secondary' : ''}`}
            onClick={() => setFilter('accepted')}
            style={{ fontSize: '0.8rem', padding: '6px 12px' }}
          >
            <Check size={14} /> Accepted ({acceptedCount})
          </button>
          <button
            className={`btn btn-ghost ${filter === 'ignored' ? 'btn-secondary' : ''}`}
            onClick={() => setFilter('ignored')}
            style={{ fontSize: '0.8rem', padding: '6px 12px' }}
          >
            <EyeOff size={14} /> Ignored ({ignoredCount})
          </button>
        </div>

        {/* Search */}
        <div style={{ flex: 1, minWidth: 200, position: 'relative' }}>
          <Search size={16} style={{
            position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)',
            color: 'var(--text-muted)',
          }} />
          <input
            type="text"
            placeholder="Search memo, payee, category…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: '100%', padding: '8px 12px 8px 34px',
              background: 'var(--bg-card)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)', color: 'var(--text-primary)',
              fontFamily: 'inherit', fontSize: '0.85rem',
              outline: 'none',
            }}
          />
        </div>

        {/* Bulk actions */}
        {selected.size > 0 && (
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-primary"
              style={{ fontSize: '0.8rem', padding: '6px 14px' }}
              onClick={() => {
                onBulkAction(Array.from(selected), 'accepted');
                setSelected(new Set());
              }}
            >
              <CheckCheck size={14} /> Accept {selected.size}
            </button>
            <button
              className="btn btn-ghost"
              style={{ fontSize: '0.8rem', padding: '6px 14px', color: 'var(--danger)' }}
              onClick={() => {
                onBulkAction(Array.from(selected), 'ignored');
                setSelected(new Set());
              }}
            >
              <X size={14} /> Ignore {selected.size}
            </button>
          </div>
        )}
      </div>

      {/* ---- Table ---- */}
      <div className="card" style={{ overflowY: 'auto', maxHeight: '65vh' }}>
        <table className="results-table">
          <colgroup>
            <col style={{ width: 36 }} />
            <col style={{ width: 95 }} />   {/* Date */}
            <col />                         {/* Memo — takes remaining space */}
            <col style={{ width: 95 }} />   {/* Amount */}
            <col style={{ width: 140 }} />  {/* Payee */}
            <col style={{ width: 140 }} />  {/* Category */}
            <col style={{ width: 110 }} />  {/* Source Cat. */}
            <col style={{ width: 105 }} />  {/* Source */}
            <col style={{ width: 90 }} />   {/* Conf. */}
            <col style={{ width: 90 }} />   {/* Status */}
            <col style={{ width: 110 }} />  {/* Actions */}
          </colgroup>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={selected.size === filtered.length && filtered.length > 0}
                  onChange={selectAll}
                  style={{ accentColor: 'var(--accent)' }}
                />
              </th>
              {([
                ['date', 'Date'],
                ['memo', 'Memo'],
                ['amount', 'Amount'],
                ['payee', 'Payee'],
                ['category', 'Category'],
                ['source_category', 'Source Cat.'],
                ['source', 'Source'],
                ['confidence', 'Conf.'],
                ['status', 'Status'],
              ] as [SortKey, string][]).map(([key, label]) => (
                <th
                  key={key}
                  onClick={() => toggleSort(key)}
                  style={{ cursor: 'pointer', userSelect: 'none' }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    {label}
                    {sortKey === key
                      ? (sortDir === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)
                      : <ArrowUpDown size={12} style={{ opacity: 0.3 }} />}
                  </span>
                </th>
              ))}
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(({ row, originalIndex }) => {
              const isEditing = editingRow === originalIndex;
              return (
                <tr
                  key={originalIndex}
                  className="animate-in"
                  style={{
                    opacity: row.status === 'ignored' ? 0.4 : 1,
                    animationDelay: `${(originalIndex % 30) * 15}ms`,
                  }}
                >
                  {/* Checkbox */}
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(originalIndex)}
                      onChange={() => toggleSelect(originalIndex)}
                      style={{ accentColor: 'var(--accent)' }}
                    />
                  </td>

                  {/* Date */}
                  <td style={{ whiteSpace: 'nowrap', textOverflow: 'clip' }}>{row.date}</td>

                  {/* Memo */}
                  <td className="no-truncate" title={row.original_memo}>
                    {row.merchant_stem || row.cleaned_memo}
                  </td>

                  {/* Amount */}
                  <td className={`amount ${row.amount >= 0 ? 'positive' : 'negative'}`} style={{ whiteSpace: 'nowrap', textOverflow: 'clip', textAlign: 'right' }}>
                    {formatAmount(row.amount)}
                  </td>

                  {/* Payee — editable */}
                  <td className="no-truncate">
                    {isEditing ? (
                      <input
                        value={row.editedPayee}
                        onChange={(e) => onUpdateRow(originalIndex, { editedPayee: e.target.value })}
                        style={{
                          width: '100%', padding: '4px 6px', background: 'var(--bg-secondary)',
                          border: '1px solid var(--accent)', borderRadius: 'var(--radius-sm)',
                          color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.8rem',
                        }}
                      />
                    ) : (
                      <span style={{ color: row.editedPayee ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                        {row.editedPayee || '—'}
                      </span>
                    )}
                  </td>

                  {/* Category — editable (select from existing) */}
                  <td className="no-truncate">
                    {isEditing ? (
                      <CategoryPicker
                        value={row.editedCategory}
                        groups={categoryGroups}
                        onChange={(name) => onUpdateRow(originalIndex, { editedCategory: name })}
                      />
                    ) : (
                      <span style={{ color: row.editedCategory ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                        {row.editedCategory || '—'}
                      </span>
                    )}
                  </td>

                  {/* Source category */}
                  <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    {row.source_category || '—'}
                  </td>

                  {/* Source badge */}
                  <td>
                    <span className={`badge ${sourceBadge(row.source)}`}>
                      {sourceIcon(row.source)} {row.source.replace('_', ' ')}
                    </span>
                  </td>

                  {/* Confidence */}
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <div className="confidence-bar">
                        <div
                          className={`confidence-bar-fill ${confLevel(row.confidence)}`}
                          style={{ width: `${row.confidence * 100}%` }}
                        />
                      </div>
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                        {(row.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                  </td>

                  {/* Status */}
                  <td>
                    {row.status === 'accepted' && (
                      <span className="badge badge-success"><Check size={12} /> Accepted</span>
                    )}
                    {row.status === 'pending' && (
                      <span className="badge badge-warning"><Eye size={12} /> Pending</span>
                    )}
                    {row.status === 'ignored' && (
                      <span className="badge badge-danger"><EyeOff size={12} /> Ignored</span>
                    )}
                  </td>

                  {/* Actions */}
                  <td style={{ overflow: 'visible' }}>
                    <div style={{ display: 'flex', gap: 4 }}>
                      {onCreateRule && row.editedPayee && (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '4px 8px' }}
                          onClick={() => onCreateRule(row)}
                          title="Create rule from this correction"
                        >
                          <Zap size={14} color="var(--accent-light)" />
                        </button>
                      )}
                      {isEditing ? (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '4px 8px' }}
                          onClick={() => setEditingRow(null)}
                          title="Done editing"
                        >
                          <Check size={14} color="var(--success)" />
                        </button>
                      ) : (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '4px 8px' }}
                          onClick={() => setEditingRow(originalIndex)}
                          title="Edit payee/category"
                        >
                          <Pencil size={14} />
                        </button>
                      )}
                      {row.status !== 'accepted' && (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '4px 8px' }}
                          onClick={() => onUpdateRow(originalIndex, { status: 'accepted' })}
                          title="Accept"
                        >
                          <Check size={14} color="var(--success)" />
                        </button>
                      )}
                      {row.status !== 'ignored' && (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '4px 8px' }}
                          onClick={() => onUpdateRow(originalIndex, { status: 'ignored' })}
                          title="Ignore"
                        >
                          <X size={14} color="var(--danger)" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

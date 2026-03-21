import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, Plus, Zap, Shield } from 'lucide-react';
import { fetchRules, createRule, deleteRule } from '../api/client';
import type { Rule, CategoryGroup, PayeeItem } from '../api/client';
import { CategoryPicker, PayeePicker } from './SearchablePicker';

interface Props {
  planId: string;
  categoryGroups: CategoryGroup[];
  payees: PayeeItem[];
}

export default function RulesManager({ planId, categoryGroups, payees }: Props) {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newRule, setNewRule] = useState({
    name: '', match_type: 'contains', pattern: '',
    assign_payee: '', assign_category: '',
  });

  const rulesQuery = useQuery({
    queryKey: ['rules', planId],
    queryFn: () => fetchRules(planId),
    enabled: !!planId,
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRule,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rules', planId] }),
  });

  const createMutation = useMutation({
    mutationFn: () => createRule({
      plan_id: planId,
      name: newRule.name,
      match_type: newRule.match_type,
      pattern: newRule.pattern,
      assign_payee: newRule.assign_payee || undefined,
      assign_category: newRule.assign_category || undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules', planId] });
      setShowCreate(false);
      setNewRule({ name: '', match_type: 'contains', pattern: '', assign_payee: '', assign_category: '' });
    },
  });

  const rules: Rule[] = rulesQuery.data || [];

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.85rem',
    outline: 'none',
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Shield size={20} color="var(--accent)" /> Classification Rules ({rules.length})
        </h3>
        <button className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={() => setShowCreate(!showCreate)}>
          <Plus size={14} /> New Rule
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="card animate-in" style={{ padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            <div>
              <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Rule Name</label>
              <input style={inputStyle} value={newRule.name} onChange={(e) => setNewRule({ ...newRule, name: e.target.value })} placeholder="e.g. Migros → Groceries" />
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Match Type</label>
              <select style={{ ...inputStyle, cursor: 'pointer' }} value={newRule.match_type} onChange={(e) => setNewRule({ ...newRule, match_type: e.target.value })}>
                <option value="contains">Contains</option>
                <option value="exact">Exact</option>
                <option value="regex">Regex</option>
              </select>
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Pattern</label>
              <input style={inputStyle} value={newRule.pattern} onChange={(e) => setNewRule({ ...newRule, pattern: e.target.value })} placeholder="MIGROS" />
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Assign Payee</label>
              <PayeePicker
                value={newRule.assign_payee}
                items={payees}
                onChange={(name) => setNewRule({ ...newRule, assign_payee: name })}
                inputStyle={inputStyle}
              />
            </div>
            <div style={{ gridColumn: 'span 2' }}>
              <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Assign Category</label>
              <CategoryPicker
                value={newRule.assign_category}
                groups={categoryGroups}
                onChange={(name) => setNewRule({ ...newRule, assign_category: name })}
                inputStyle={inputStyle}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn btn-ghost" onClick={() => setShowCreate(false)}>Cancel</button>
            <button
              className="btn btn-primary"
              onClick={() => createMutation.mutate()}
              disabled={!newRule.name || !newRule.pattern || createMutation.isPending}
            >
              {createMutation.isPending ? 'Creating…' : 'Create Rule'}
            </button>
          </div>
        </div>
      )}

      {/* Rules list */}
      {rules.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: 'var(--text-muted)' }}>
          <Zap size={32} style={{ marginBottom: 8, color: 'var(--accent-subtle)' }} />
          <p>No rules yet. Create one to auto-classify transactions.</p>
        </div>
      ) : (
        <div className="card" style={{ overflow: 'auto' }}>
          <table className="results-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Pattern</th>
                <th>→ Payee</th>
                <th>→ Category</th>
                <th>Priority</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{r.name}</td>
                  <td><span className="badge badge-accent">{r.match_type}</span></td>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{r.pattern}</td>
                  <td>{r.assign_payee || '—'}</td>
                  <td>{r.assign_category || '—'}</td>
                  <td style={{ textAlign: 'center' }}>{r.priority}</td>
                  <td>
                    <button
                      className="btn btn-ghost"
                      style={{ padding: '4px 8px' }}
                      onClick={() => deleteMutation.mutate(r.id)}
                      title="Delete rule"
                    >
                      <Trash2 size={14} color="var(--danger)" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2, Plus, Zap, Shield, Pencil, Check, X } from 'lucide-react';
import { fetchRules, createRule, updateRule, deleteRule } from '../api/client';
import type { Rule, CategoryGroup, PayeeItem } from '../api/client';
import { CategoryPicker, PayeePicker } from './SearchablePicker';

interface Props {
  planId: string;
  categoryGroups: CategoryGroup[];
  payees: PayeeItem[];
  onRuleChanged?: () => void;
}

type RuleFormData = {
  name: string;
  match_type: string;
  pattern: string;
  assign_payee: string;
  assign_category: string;
};

const emptyForm: RuleFormData = {
  name: '', match_type: 'contains', pattern: '',
  assign_payee: '', assign_category: '',
};

export default function RulesManager({ planId, categoryGroups, payees, onRuleChanged }: Props) {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [formData, setFormData] = useState<RuleFormData>({ ...emptyForm });

  const rulesQuery = useQuery({
    queryKey: ['rules', planId],
    queryFn: () => fetchRules(planId),
    enabled: !!planId,
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules', planId] });
      onRuleChanged?.();
    },
  });

  const createMutation = useMutation({
    mutationFn: () => createRule({
      plan_id: planId,
      name: formData.name,
      match_type: formData.match_type,
      pattern: formData.pattern,
      assign_payee: formData.assign_payee || undefined,
      assign_category: formData.assign_category || undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules', planId] });
      setShowCreate(false);
      setFormData({ ...emptyForm });
      onRuleChanged?.();
    },
  });

  const updateMutation = useMutation({
    mutationFn: (ruleId: string) => updateRule(ruleId, {
      plan_id: planId,
      name: formData.name,
      match_type: formData.match_type,
      pattern: formData.pattern,
      assign_payee: formData.assign_payee || undefined,
      assign_category: formData.assign_category || undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules', planId] });
      setEditingRuleId(null);
      setFormData({ ...emptyForm });
      onRuleChanged?.();
    },
  });

  const rules: Rule[] = rulesQuery.data || [];

  const startEditing = (rule: Rule) => {
    setEditingRuleId(rule.id);
    setShowCreate(false);
    setFormData({
      name: rule.name,
      match_type: rule.match_type,
      pattern: rule.pattern,
      assign_payee: rule.assign_payee || '',
      assign_category: rule.assign_category || '',
    });
  };

  const cancelEditing = () => {
    setEditingRuleId(null);
    setFormData({ ...emptyForm });
  };

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px', background: 'var(--bg-secondary)',
    border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.85rem',
    outline: 'none',
  };

  const renderForm = (onSave: () => void, onCancel: () => void, saveLabel: string, isPending: boolean) => (
    <div className="card animate-in" style={{ padding: 20, marginBottom: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
        <div>
          <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Rule Name</label>
          <input style={inputStyle} value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} placeholder="e.g. Migros → Groceries" />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Match Type</label>
          <select style={{ ...inputStyle, cursor: 'pointer' }} value={formData.match_type} onChange={(e) => setFormData({ ...formData, match_type: e.target.value })}>
            <option value="contains">Contains</option>
            <option value="exact">Exact</option>
            <option value="regex">Regex</option>
          </select>
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Pattern</label>
          <input style={inputStyle} value={formData.pattern} onChange={(e) => setFormData({ ...formData, pattern: e.target.value })} placeholder="MIGROS" />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Assign Payee</label>
          <PayeePicker
            value={formData.assign_payee}
            items={payees}
            onChange={(name) => setFormData({ ...formData, assign_payee: name })}
            inputStyle={inputStyle}
          />
        </div>
        <div style={{ gridColumn: 'span 2' }}>
          <label style={{ display: 'block', marginBottom: 4, fontSize: '0.8rem', color: 'var(--text-muted)' }}>Assign Category</label>
          <CategoryPicker
            value={formData.assign_category}
            groups={categoryGroups}
            onChange={(name) => setFormData({ ...formData, assign_category: name })}
            inputStyle={inputStyle}
          />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button className="btn btn-ghost" onClick={onCancel}>Cancel</button>
        <button
          className="btn btn-primary"
          onClick={onSave}
          disabled={!formData.name || !formData.pattern || isPending}
        >
          {isPending ? 'Saving…' : saveLabel}
        </button>
      </div>
    </div>
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Shield size={20} color="var(--accent)" /> Classification Rules ({rules.length})
        </h3>
        <button className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={() => {
          setShowCreate(!showCreate);
          setEditingRuleId(null);
          setFormData({ ...emptyForm });
        }}>
          <Plus size={14} /> New Rule
        </button>
      </div>

      {/* Create form */}
      {showCreate && renderForm(
        () => createMutation.mutate(),
        () => setShowCreate(false),
        'Create Rule',
        createMutation.isPending,
      )}

      {/* Edit form (shown above the table) */}
      {editingRuleId && renderForm(
        () => updateMutation.mutate(editingRuleId),
        cancelEditing,
        'Save Changes',
        updateMutation.isPending,
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
                <th>Payee</th>
                <th>Category</th>
                <th>Priority</th>
                <th style={{ width: 80 }}></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id} style={editingRuleId === r.id ? { background: 'var(--accent-subtle)' } : undefined}>
                  <td style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{r.name}</td>
                  <td><span className="badge badge-accent">{r.match_type}</span></td>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{r.pattern}</td>
                  <td>{r.assign_payee || '—'}</td>
                  <td>{r.assign_category || '—'}</td>
                  <td style={{ textAlign: 'center' }}>{r.priority}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 4 }}>
                      {editingRuleId === r.id ? (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '4px 8px' }}
                          onClick={cancelEditing}
                          title="Cancel editing"
                        >
                          <X size={14} color="var(--text-muted)" />
                        </button>
                      ) : (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '4px 8px' }}
                          onClick={() => startEditing(r)}
                          title="Edit rule"
                        >
                          <Pencil size={14} />
                        </button>
                      )}
                      <button
                        className="btn btn-ghost"
                        style={{ padding: '4px 8px' }}
                        onClick={() => deleteMutation.mutate(r.id)}
                        title="Delete rule"
                      >
                        <Trash2 size={14} color="var(--danger)" />
                      </button>
                    </div>
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

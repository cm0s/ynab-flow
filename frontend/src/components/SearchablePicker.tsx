import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import type { CategoryGroup } from '../api/client';

/* ---- Grouped picker (for categories) ---- */
export function CategoryPicker({
  value,
  groups,
  onChange,
  inputStyle,
}: {
  value: string;
  groups: CategoryGroup[];
  onChange: (name: string) => void;
  inputStyle?: React.CSSProperties;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (open && inputRef.current) {
      const rect = inputRef.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 2, left: rect.left });
    }
  }, [open, query]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        inputRef.current && !inputRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const q = query.toLowerCase();
  const filtered = groups
    .map((g) => ({
      ...g,
      categories: g.categories.filter((c) => c.name.toLowerCase().includes(q)),
    }))
    .filter((g) => g.categories.length > 0);

  return (
    <>
      <input
        ref={inputRef}
        autoFocus
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        placeholder={value || 'Search category\u2026'}
        style={inputStyle ?? {
          width: '100%', boxSizing: 'border-box', padding: '4px 6px', background: 'var(--bg-secondary)',
          border: '1px solid var(--accent)', borderRadius: 'var(--radius-sm)',
          color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.8rem',
        }}
      />
      {open && pos && createPortal(
        <div
          ref={dropdownRef}
          style={{
            position: 'fixed', top: pos.top, left: pos.left, zIndex: 9999,
            width: 280, maxHeight: 260, overflowY: 'auto',
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)', boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          }}
        >
          {filtered.length === 0 && (
            <div style={{ padding: '8px 12px', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
              No categories found
            </div>
          )}
          {filtered.map((g) => (
            <div key={g.id}>
              <div style={{
                padding: '6px 12px', fontSize: '0.7rem', fontWeight: 600,
                color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em',
                background: 'var(--bg-surface)', position: 'sticky', top: 0,
              }}>
                {g.name}
              </div>
              {g.categories.map((c) => (
                <div
                  key={c.id}
                  onMouseDown={(e) => { e.preventDefault(); onChange(c.name); setOpen(false); setQuery(''); }}
                  style={{
                    padding: '6px 12px 6px 20px', fontSize: '0.8rem', cursor: 'pointer',
                    color: c.name === value ? 'var(--accent)' : 'var(--text-primary)',
                    background: c.name === value ? 'var(--bg-surface)' : 'transparent',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-surface)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = c.name === value ? 'var(--bg-surface)' : 'transparent')}
                >
                  {c.name}
                </div>
              ))}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}

/* ---- Flat list picker (for payees) ---- */
export function PayeePicker({
  value,
  items,
  onChange,
  inputStyle,
}: {
  value: string;
  items: { id: string; name: string }[];
  onChange: (name: string) => void;
  inputStyle?: React.CSSProperties;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (open && inputRef.current) {
      const rect = inputRef.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 2, left: rect.left });
    }
  }, [open, query]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        inputRef.current && !inputRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const q = query.toLowerCase();
  const filtered = q
    ? items.filter((p) => p.name.toLowerCase().includes(q))
    : items;
  const display = filtered.slice(0, 50); // cap for performance
  const exactMatch = q && items.some((p) => p.name.toLowerCase() === q);

  const submitQuery = () => {
    const trimmed = query.trim();
    if (trimmed) {
      onChange(trimmed);
      setOpen(false);
      setQuery('');
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => { if (query.trim()) submitQuery(); }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); submitQuery(); }
          if (e.key === 'Escape') { setOpen(false); setQuery(''); }
        }}
        placeholder={value || 'Search payee\u2026'}
        style={inputStyle ?? {
          width: '100%', boxSizing: 'border-box', padding: '4px 6px', background: 'var(--bg-secondary)',
          border: '1px solid var(--accent)', borderRadius: 'var(--radius-sm)',
          color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.8rem',
        }}
      />
      {open && pos && createPortal(
        <div
          ref={dropdownRef}
          style={{
            position: 'fixed', top: pos.top, left: pos.left, zIndex: 9999,
            width: 280, maxHeight: 260, overflowY: 'auto',
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)', boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          }}
        >
          {q && !exactMatch && (
            <div
              onMouseDown={(e) => { e.preventDefault(); submitQuery(); }}
              style={{
                padding: '6px 12px', fontSize: '0.8rem', cursor: 'pointer',
                color: 'var(--accent)', borderBottom: '1px solid var(--border)',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-surface)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              Use "{query.trim()}"
            </div>
          )}
          {display.map((p) => (
            <div
              key={p.id}
              onMouseDown={(e) => { e.preventDefault(); onChange(p.name); setOpen(false); setQuery(''); }}
              style={{
                padding: '6px 12px', fontSize: '0.8rem', cursor: 'pointer',
                color: p.name === value ? 'var(--accent)' : 'var(--text-primary)',
                background: p.name === value ? 'var(--bg-surface)' : 'transparent',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-surface)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = p.name === value ? 'var(--bg-surface)' : 'transparent')}
            >
              {p.name}
            </div>
          ))}
          {display.length === 0 && exactMatch && null}
          {filtered.length > 50 && (
            <div style={{ padding: '6px 12px', fontSize: '0.75rem', color: 'var(--text-muted)', textAlign: 'center' }}>
              {filtered.length - 50} more — type to narrow results
            </div>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}
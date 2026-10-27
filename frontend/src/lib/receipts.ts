/** Labels and styles shared by the Receipts page components. */
import type { CSSProperties } from 'react';

export const STATUS_LABELS: Record<string, string> = {
  extracted: 'Read', needs_check: 'Needs a check', candidate: 'Waiting for AI', not_receipt: 'Not a receipt',
  error: 'Failed',
};

export const KIND_LABELS: Record<string, string> = {
  purchase: 'Purchase', refund: 'Refund', shipping: 'Shipping notice', payment: 'Payment notice',
};

export const CATEGORY_SOURCE_LABELS: Record<string, string> = {
  user: 'yours', split: 'split line', row: 'bank row', ai: 'AI', uncategorized: 'uncategorized',
};

export const inputStyle: CSSProperties = {
  padding: '6px 10px', background: 'var(--bg-secondary)', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: '0.85rem',
};

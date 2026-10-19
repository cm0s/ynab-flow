/** Pure helpers for the import/push workflow (kept free of React so they can be tested). */

import type { WriteBackResponse, ImportRowData } from '../api/client';

export interface HasId { id: string }

/** Statuses meaning YNAB confirmed the row is handled (created now, or already there). */
const HANDLED = new Set(['created', 'existing']);

/** Ids of source rows YNAB confirmed. Dry runs and errors/unresolved rows never count. */
export function handledRowIds(result: Pick<WriteBackResponse, 'mode' | 'results'>): Set<string> {
  if (result.mode === 'dry_run') return new Set();
  return new Set(result.results.filter((r) => HANDLED.has(r.status)).map((r) => r.row_id));
}

/** Remove only confirmed rows; failures, unresolved and unsent rows stay for retry. */
export function removeHandled<T extends HasId>(rows: T[], handled: Set<string>): T[] {
  return rows.filter((r) => !handled.has(r.id));
}

/** A batch can complete when nothing is left that still needs a decision or a retry. */
export function canCompleteBatch(rows: { status: string }[]): boolean {
  return rows.every((r) => r.status === 'ignored');
}

/** Rows that need user attention before they can be written (repayments, reconciliation). */
const BLOCKING_RECONCILIATION = new Set(['potential', 'conflict', 'deleted_link', 'unverified', 'ambiguous', 'legacy_unresolved', 'quarantined']);

export function needsResolution(row: Pick<ImportRowData, 'transfer_state' | 'reconciliation_state'>): boolean {
  return row.transfer_state === 'candidate' || row.transfer_state === 'ambiguous'
    || BLOCKING_RECONCILIATION.has(row.reconciliation_state ?? '');
}

/** Human summary of an import for the review header. */
export function describeImport(report: { detected?: number; eligible?: number; pending?: number; invalid?: number; already_imported?: number } | undefined): string {
  if (!report || report.detected === undefined) return '';
  const parts = [`${report.detected} detected`, `${report.eligible ?? 0} eligible`];
  if (report.pending) parts.push(`${report.pending} pending (not imported)`);
  if (report.invalid) parts.push(`${report.invalid} invalid`);
  if (report.already_imported) parts.push(`${report.already_imported} already in YNAB`);
  return parts.join(' · ');
}

export const SOURCE_LABELS: Record<string, string> = {
  swisscard: 'Swisscard',
  postfinance: 'PostFinance',
  generic: 'Generic CSV',
  legacy: 'Earlier import',
};

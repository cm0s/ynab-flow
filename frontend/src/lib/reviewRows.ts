/** Turns import rows from the API into review-table rows. */
import type { ImportRowData } from '../api/client';
import type { FlagColor, ReviewedRow, ReviewStatus } from '../components/ReviewTable';

export function toReviewedRows(predictions: ImportRowData[]): ReviewedRow[] {
  return predictions.map((p) => ({
    ...p,
    id: p.id,
    status: (p.status as ReviewStatus) || (p.review_required ? 'pending' : 'accepted'),
    editedPayee: p.edited_payee ?? p.payee ?? '',
    editedCategory: p.edited_category ?? p.category ?? '',
    editedCategoryId: p.edited_category_id ?? p.category_id ?? null,
    editedMemo: p.edited_memo || '',
    flagColor: (p.flag_color || '') as FlagColor,
  }));
}

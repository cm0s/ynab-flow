/** Exact split arithmetic in integer milliunits (no binary floats), kept free of React for testing. */

export type SplitOrigin = 'receipt' | 'ai' | 'user';

export interface SplitLineData {
  amount_milliunits: number;
  category_id: string;
  payee?: string;
  memo?: string;
  origin?: SplitOrigin;
  category_name?: string | null;
}

/** One editable line: the amount is typed without a sign; the sign comes from the bank amount. */
export interface SplitDraft {
  amount: string;
  categoryId: string;
  memo: string;
  origin: SplitOrigin;
}

/** Parse "62.40", "62,4" or "1'062.40" into milliunits; null when not an exact amount (max 3 decimals). */
export function parseMilliunits(text: string): number | null {
  const match = /^(\d+)(?:[.,](\d{0,3}))?$/.exec(text.replace(/['’\s]/g, ''));
  if (!match) return null;
  const whole = Number(match[1]);
  const fraction = Number((match[2] ?? '').padEnd(3, '0'));
  const value = whole * 1000 + fraction;
  return Number.isSafeInteger(value) ? value : null;
}

/** "62.40" for 62400 (sign dropped); a third decimal only when needed. */
export function formatMilliunits(milliunits: number): string {
  const abs = Math.abs(milliunits);
  const whole = Math.floor(abs / 1000);
  const fraction = String(abs % 1000).padStart(3, '0');
  return fraction.endsWith('0') ? `${whole}.${fraction.slice(0, 2)}` : `${whole}.${fraction}`;
}

const sign = (bank: number) => (bank < 0 ? -1 : 1);

export function draftsFromLines(lines: SplitLineData[]): SplitDraft[] {
  return lines.map((l) => ({
    amount: formatMilliunits(l.amount_milliunits),
    categoryId: l.category_id,
    memo: l.memo ?? '',
    origin: l.origin ?? 'user',
  }));
}

/** Bank amount not yet covered by the drafts, signed like the bank amount (0 when it adds up). */
export function unassignedMilliunits(bank: number, drafts: SplitDraft[]): number {
  const assigned = drafts.reduce((sum, d) => sum + (parseMilliunits(d.amount) ?? 0), 0);
  return bank - sign(bank) * assigned;
}

/** Lines to save, or a message saying what still needs fixing. */
export function linesFromDrafts(bank: number, drafts: SplitDraft[]): SplitLineData[] | string {
  if (drafts.length < 2) return 'A split needs at least two lines.';
  const lines: SplitLineData[] = [];
  for (const [i, d] of drafts.entries()) {
    const amount = parseMilliunits(d.amount);
    if (amount === null || amount === 0) return `Line ${i + 1}: enter an amount such as 12.50.`;
    if (!d.categoryId) return `Line ${i + 1}: choose a category.`;
    lines.push({ amount_milliunits: sign(bank) * amount, category_id: d.categoryId, memo: d.memo, origin: d.origin });
  }
  const gap = unassignedMilliunits(bank, drafts);
  if (gap !== 0) {
    const over = Math.abs(bank - gap) > Math.abs(bank);
    return `${formatMilliunits(gap)} is ${over ? 'over-assigned' : 'unassigned'}.`;
  }
  return lines;
}

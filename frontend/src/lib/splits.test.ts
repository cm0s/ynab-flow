// Split arithmetic checks: run with `pnpm run test:flow` (node's built-in runner).
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseMilliunits, formatMilliunits, unassignedMilliunits, linesFromDrafts, draftsFromLines, type SplitDraft } from './splits.ts';

const draft = (amount: string, categoryId = 'c1'): SplitDraft => ({ amount, categoryId, memo: '', origin: 'user' });

test('amounts parse exactly to milliunits', () => {
  assert.equal(parseMilliunits('62.40'), 62400);
  assert.equal(parseMilliunits('62,4'), 62400);
  assert.equal(parseMilliunits("1'062.405"), 1062405);
  assert.equal(parseMilliunits('0.1'), 100);
  assert.equal(parseMilliunits('84.905 '), 84905);
  assert.equal(parseMilliunits('1.2345'), null);
  assert.equal(parseMilliunits('-5'), null);
  assert.equal(parseMilliunits('abc'), null);
});

test('formatting drops the sign and keeps cents', () => {
  assert.equal(formatMilliunits(-84900), '84.90');
  assert.equal(formatMilliunits(5), '0.005');
  assert.equal(formatMilliunits(120000), '120.00');
});

test('unassigned amount follows the bank sign', () => {
  assert.equal(unassignedMilliunits(-84900, [draft('62.40'), draft('22.45')]), -50);
  assert.equal(unassignedMilliunits(-84900, [draft('62.40'), draft('22.50')]), 0);
  assert.equal(unassignedMilliunits(25000, [draft('20'), draft('')]), 5000);
  // 0.1 + 0.2 style sums stay exact
  assert.equal(unassignedMilliunits(-300, [draft('0.1'), draft('0.2')]), 0);
});

test('drafts become signed lines only when they add up', () => {
  assert.equal(linesFromDrafts(-84900, [draft('62.40'), draft('22.45')]), '0.05 is unassigned.');
  assert.equal(linesFromDrafts(-84900, [draft('62.40'), draft('23.00')]), '0.50 is over-assigned.');
  assert.equal(linesFromDrafts(-84900, [draft('84.90')]), 'A split needs at least two lines.');
  assert.equal(linesFromDrafts(-84900, [draft('62.40'), draft('22.50', '')]), 'Line 2: choose a category.');
  const lines = linesFromDrafts(-84900, [draft('62.40', 'groc'), draft('22.50', 'home')]);
  assert.deepEqual(lines, [
    { amount_milliunits: -62400, category_id: 'groc', memo: '', origin: 'user' },
    { amount_milliunits: -22500, category_id: 'home', memo: '', origin: 'user' },
  ]);
  assert.deepEqual(draftsFromLines(lines as never).map((d) => d.amount), ['62.40', '22.50']);
});

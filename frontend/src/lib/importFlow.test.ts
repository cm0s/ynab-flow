// Local mocked workflow checks: run with `pnpm run test:flow` (node's built-in runner).
import test from 'node:test';
import assert from 'node:assert/strict';
import { handledRowIds, removeHandled, canCompleteBatch, needsResolution, describeImport } from './importFlow.ts';

const result = (mode: string, statuses: [string, string][]) => ({
  mode,
  results: statuses.map(([row_id, status]) => ({ row_id, status })),
}) as never;

test('mixed success removes only confirmed rows', () => {
  const rows = [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }];
  const handled = handledRowIds(result('create', [['a', 'created'], ['b', 'existing'], ['c', 'error'], ['d', 'unresolved']]));
  assert.deepEqual(removeHandled(rows, handled).map((r) => r.id), ['c', 'd']);
});

test('duplicate-only success completes the rows', () => {
  const rows = [{ id: 'a' }];
  assert.deepEqual(removeHandled(rows, handledRowIds(result('create', [['a', 'existing']]))), []);
});

test('dry runs retain every review row', () => {
  const rows = [{ id: 'a' }, { id: 'b' }];
  const handled = handledRowIds(result('dry_run', [['a', 'dry_run_ok'], ['b', 'existing']]));
  assert.equal(handled.size, 0);
  assert.equal(removeHandled(rows, handled).length, 2);
});

test('retry after failure then completion', () => {
  let rows = [{ id: 'a', status: 'accepted' }, { id: 'b', status: 'accepted' }];
  rows = removeHandled(rows, handledRowIds(result('create', [['a', 'created'], ['b', 'error']])));
  assert.equal(canCompleteBatch(rows), false);
  rows = removeHandled(rows, handledRowIds(result('create', [['b', 'created']])));
  assert.equal(canCompleteBatch(rows), true);
  assert.equal(canCompleteBatch([{ status: 'ignored' }]), true);
});

test('repayment and reconciliation blocks need resolution', () => {
  assert.equal(needsResolution({ transfer_state: 'candidate', reconciliation_state: 'pending' }), true);
  assert.equal(needsResolution({ transfer_state: 'none', reconciliation_state: 'potential' }), true);
  assert.equal(needsResolution({ transfer_state: 'resolved', reconciliation_state: 'new' }), false);
  assert.equal(needsResolution({ transfer_state: 'none', reconciliation_state: 'pending' }), false);
});

test('import summary shows counts', () => {
  assert.equal(describeImport({ detected: 174, eligible: 167, pending: 7, invalid: 0 }),
    '174 detected · 167 eligible · 7 pending (not imported)');
  assert.equal(describeImport(undefined), '');
});

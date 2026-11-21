import { afterEach, expect, it, vi } from 'vitest';
import { fetchCoachOptions } from '../staffUsers';

afterEach(() => vi.unstubAllGlobals());

it('offers only staff who hold the actual Coach access grant', async () => {
  const rows = [
    { id: '1', name: 'Case Owner Only', email: 'owner@example.test', position: 'Caseowner', access: 'enrolment', accesses: ['enrolment'] },
    { id: '2', name: 'Primary Coach', email: 'primary@example.test', position: 'Admin', access: 'coach', accesses: ['coach'] },
    { id: '3', name: 'Additional Coach', email: 'additional@example.test', position: 'Tutor', access: 'tutor', accesses: ['tutor', 'coach'] },
  ];
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ count: rows.length, results: rows }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })));

  await expect(fetchCoachOptions()).resolves.toEqual([
    { name: 'Primary Coach', email: 'primary@example.test' },
    { name: 'Additional Coach', email: 'additional@example.test' },
  ]);
});

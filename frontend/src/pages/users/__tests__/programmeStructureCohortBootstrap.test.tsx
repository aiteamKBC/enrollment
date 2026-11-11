import { describe, expect, it } from 'vitest';
import { normaliseCurriculumCohorts } from '../programme-structure/cohortBootstrap';

describe('curriculum cohort bootstrap', () => {
  it('normalises the real curriculum collection response and preserves the Feb 2027 ID', () => {
    const rows = normaliseCurriculumCohorts({ schema: 'v1', count: 1, results: [{ id: 'COHORT-20260917092025244736D2820D958B44', name: 'Feb 2027', programmeId: 'PROG-ME-L4', status: 'planned' }] });
    expect(rows).toEqual([{ id: 'COHORT-20260917092025244736D2820D958B44', name: 'Feb 2027', programmeId: 'PROG-ME-L4', programme: '', startDate: '', status: 'planned' }]);
  });
});

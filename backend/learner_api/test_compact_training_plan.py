"""Pure display-summary tests: no database connection or mutation."""
import copy
from unittest import TestCase
from .compact_training_plan import summarise, title


class CompactTrainingPlanTests(TestCase):
    def module(self):
        return {'academicModuleId': 'academic', 'lmsOfferingId': 'offering', 'hours': 138,
                'weeks': [{'sourceId': 'w'+str(i), 'title': 'L'+str(i)+': Real title '+str(i)} for i in range(1,6)],
                'activities': [dict(id='c'+str(i), weekId='w'+str(i), source='LMS component',
                                    title='Component title', kind='reading', hours=2.5) for i in range(1,6)]}

    def test_each_week_once_preserves_titles_and_total(self):
        module = self.module(); before = copy.deepcopy(module)
        rows, diagnostics = summarise(module)
        self.assertEqual([r['id'] for r in rows], ['w1','w2','w3','w4','w5'])
        self.assertEqual([r['title'] for r in rows], ['Real title '+str(i) for i in range(1,6)])
        self.assertEqual([r['hours'] for r in rows], [2.5]*5)
        self.assertEqual(rows[3]['kind'], 'topic')
        discrepancy = next(d for d in diagnostics if d['code'] == 'weekly_module_otj_discrepancy')
        self.assertEqual((discrepancy['moduleHours'], discrepancy['weekHours']), (138,12.5))
        self.assertEqual(discrepancy['lmsOfferingId'], 'offering')
        self.assertEqual(module, before)

    def test_assignment_depends_on_real_component_not_fourth_position(self):
        module = self.module()
        module['activities'].append(dict(id='assignment',weekId='w2',source='LMS component',kind='assignment',hours=8,date='2027-02-19'))
        rows, diagnostics = summarise(module)
        self.assertEqual(rows[1]['kind'], 'assignment')
        self.assertEqual(rows[1]['title'], 'Real title 2')
        self.assertEqual(rows[1]['hours'], 10.5)
        self.assertEqual(rows[1]['dueDates'], ['2027-02-19'])
        self.assertEqual(rows[3]['kind'], 'topic')
        self.assertTrue(any(d['code'] == 'fourth_week_has_no_assignment' for d in diagnostics))

    def test_duplicates_child_topics_and_meetings_do_not_add_hours(self):
        module = self.module()
        module['activities'] += [copy.deepcopy(module['activities'][0]),
            dict(id='child', weekId='w1',source='LMS assignment topic',parentComponentId='c1',hours=100),
            dict(id='meeting',weekId='w1',source='Programme review projection',kind='meeting',hours=1)]
        rows, diagnostics = summarise(module)
        self.assertEqual(rows[0]['hours'], 2.5)
        self.assertEqual(rows[0]['componentIds'], ['c1'])
        self.assertTrue(any(d['code'] == 'duplicate_week_component' for d in diagnostics))

    def test_zero_missing_empty_week_and_incomplete_component_read(self):
        module = self.module()
        module['activities'][0]['hours'] = 0
        module['activities'][1]['hours'] = None
        module['activities'] = module['activities'][:2]
        module['weeks'][3]['componentsAvailable'] = False
        rows, diagnostics = summarise(module)
        self.assertEqual(rows[0]['hours'], 0)
        self.assertIsNone(rows[1]['hours'])
        self.assertEqual(rows[1]['missingHoursComponentIds'], ['c2'])
        self.assertEqual(rows[2]['hours'], 0)
        self.assertIsNone(rows[3]['hours'])
        self.assertFalse(any(d['code'] == 'weekly_module_otj_discrepancy' for d in diagnostics))

    def test_strip_only_leading_lesson_markers(self):
        for original, expected in [('L1: Definitions','Definitions'), ('L12:Place','Place'),
                ('L3 - Actual L4 title 2','Actual L4 title 2'), ('London 2027','London 2027'),
                ('Level 4','Level 4'), ('L1','L1')]:
            self.assertEqual(title(original), expected)

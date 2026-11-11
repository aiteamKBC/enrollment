import copy

from django.conf import settings
from django.test import SimpleTestCase

from .modules_configuration import defaults
from .programme_structure import from_legacy
from .publication import PublicationBlocked, build_published_layout, compare_structures, preflight, publish


class ProgrammeStructurePublicationTests(SimpleTestCase):
    def setUp(self):
        self.snapshot = from_legacy(defaults(), {})
        self.layout = {
            "steps": [{"slug": "welcome", "items": []}],
            "customSetting": {"keep": True},
            "programmeStructure": self.snapshot,
        }

    def test_feature_flag_is_disabled_by_default(self):
        self.assertFalse(settings.PROGRAMME_STRUCTURE_PUBLISHING_ENABLED)
        with self.assertRaisesRegex(PublicationBlocked, "disabled"):
            publish()

    def test_preflight_is_read_only_and_build_preserves_unrelated_layout(self):
        before = copy.deepcopy(self.layout)
        result = preflight(self.layout, current_version=12, base_version=12)
        self.assertTrue(result["ok"])
        self.assertEqual(self.layout, before)
        published = build_published_layout(self.layout, result)
        self.assertEqual(published["customSetting"], {"keep": True})
        self.assertEqual(published["programmeStructure"]["version"], self.snapshot["version"])

    def test_stale_base_version_is_blocked(self):
        with self.assertRaisesRegex(PublicationBlocked, "Published settings changed"):
            preflight(self.layout, current_version=13, base_version=12)

    def test_cohort_publication_preserves_legacy_pcp_catalogue_and_rules_exactly(self):
        modules = defaults()
        catalogues = {'PCP_L6': {'programmeId': 'PCP_L6', 'version': 'historical-pcp',
            'ownerAllocation': {'K1': 'operational'}, 'records': [{
                'id': 'pcp-k1', 'code': 'K1', 'kind': 'Knowledge', 'description': 'Owner allocated'}]}}
        snapshot = from_legacy(modules, catalogues)
        snapshot['programmes'][0]['cohorts'][0]['modules'][0]['sessions'] = 19
        previous = {'steps': [{'slug': 'welcome', 'items': []}],
                    'programmeModules': modules, 'skillsRadarCatalogues': catalogues}
        result = preflight({'programmeStructure': snapshot}, current_version=16, base_version=16)
        built = build_published_layout(previous, result)
        self.assertEqual(built['programmeModules'], modules)
        self.assertEqual(built['skillsRadarCatalogues'], catalogues)
        self.assertEqual(built['programmeStructure'], snapshot)
        self.assertNotIn('programmeStructure', previous)

    def test_unsafe_legacy_projection_changes_are_rejected(self):
        previous = {'programmeModules': defaults(), 'skillsRadarCatalogues': {}}
        snapshot = from_legacy(previous['programmeModules'], {})
        snapshot['programmes'][0]['modules'][0]['sessions'] = 999
        result = preflight({'programmeStructure': snapshot}, current_version=16, base_version=16)
        with self.assertRaisesRegex(PublicationBlocked, 'legacy data changed'):
            build_published_layout(previous, result)

    def test_preview_reports_cohort_session_changes_and_calendar_changes(self):
        from .intake_calendar import DEFAULT_INTAKES
        proposed = copy.deepcopy(self.snapshot)
        proposed['programmes'][0]['cohorts'][0]['modules'][0]['sessions'] = 17
        proposed['intakeCalendar'] = copy.deepcopy(DEFAULT_INTAKES)
        changes = compare_structures(self.snapshot, proposed)
        cohort = next(c for c in changes if c['type'] == 'cohort-modified')
        self.assertEqual(cohort['after']['modules'][0]['sessions'], 17)
        self.assertTrue(any(c['type'] == 'intake-calendar-modified' for c in changes))

    def test_calendar_changes_have_distinct_snapshot_identity(self):
        from .programme_structure import version
        from .intake_calendar import DEFAULT_INTAKES
        changed = copy.deepcopy(self.snapshot)
        changed['intakeCalendar'] = copy.deepcopy(DEFAULT_INTAKES)
        self.assertNotEqual(version(changed), version(self.snapshot))

    def test_official_layout_normalizer_retains_canonical_and_calendar(self):
        from enrolment_api.wizard_layout import normalize_layout
        from .intake_calendar import DEFAULT_INTAKES
        raw = copy.deepcopy(self.layout)
        raw['steps'][0]['label'] = 'Welcome'
        raw['intakeCalendar'] = copy.deepcopy(DEFAULT_INTAKES)
        normalized, _ = normalize_layout(raw, None)
        self.assertEqual(normalized['programmeStructure'], self.snapshot)
        self.assertEqual(normalized['intakeCalendar'], DEFAULT_INTAKES)
        # Publishing an older builder payload must not remove the canonical data.
        retained, _ = normalize_layout({'steps': raw['steps']}, normalized)
        self.assertEqual(retained['programmeStructure'], self.snapshot)
        self.assertEqual(retained['intakeCalendar'], DEFAULT_INTAKES)

    def test_stale_draft_revision_is_blocked(self):
        with self.assertRaisesRegex(PublicationBlocked, "shared draft changed"):
            preflight(self.layout, current_version=12, base_version=12, draft_revision=4, expected_revision=5)

    def test_unsupported_programme_is_blocked(self):
        layout = copy.deepcopy(self.layout)
        layout["programmeStructure"]["programmes"].append({"id": "NEW", "title": "New", "modules": [], "ksbs": [], "mappings": []})
        with self.assertRaisesRegex(PublicationBlocked, "Unsupported"):
            preflight(layout, current_version=12, base_version=12)

    def test_change_comparison_detects_equal_count_mapping_move(self):
        published = copy.deepcopy(self.snapshot)
        proposed = copy.deepcopy(self.snapshot)
        published['programmes'][0]['ksbs'] = [{'id': 'k1', 'code': 'K1', 'kind': 'Knowledge', 'description': 'KSB', 'status': 'active'}]
        published['programmes'][0]['mappings'] = [{'programmeId': published['programmes'][0]['id'], 'ksbId': 'k1', 'moduleId': published['programmes'][0]['modules'][0]['id'], 'status': 'active'}]
        p = proposed['programmes'][0]
        if not p['ksbs']:
            p['ksbs'] = [{'id': 'k1', 'code': 'K1', 'kind': 'Knowledge', 'description': 'KSB', 'status': 'active'}]
        if len(p['modules']) < 2:
            p['modules'].append({'id': 'extra', 'title': 'Extra', 'selection': 'optional', 'order': 99})
        p['mappings'] = [*p.get('mappings', []), {'programmeId': p['id'], 'ksbId': p['ksbs'][0]['id'], 'moduleId': p['modules'][-1]['id'], 'status': 'active'}]
        old_rel = published['programmes'][0].get('mappings', [])
        if old_rel:
            p['mappings'] = [r for r in p['mappings'] if not (r['ksbId'] == old_rel[0]['ksbId'] and r['moduleId'] == old_rel[0]['moduleId'])]
        changes = compare_structures(published, proposed)
        self.assertTrue(any(c['type'] == 'relationship-added' for c in changes))
        self.assertTrue(any(c['type'] == 'relationship-removed' for c in changes))

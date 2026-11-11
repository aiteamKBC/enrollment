from django.test import SimpleTestCase
from .programme_structure import applicable_ksbs, from_legacy, project_catalogues, validate_selection, version, validate_structure, semantic_parity, resolve_pcp_shadow, validate_pcp_shadow_selection, shadow_version_context, shadow_compare_learner, resolve_assigned_cohort
from .modules_configuration import defaults
from .modules_service import validate_snapshot_choices
from .modules_rules import available_modules
from unittest.mock import Mock

class ProgrammeStructureTests(SimpleTestCase):
    def setUp(self):
        self.modules = {"programmes": [{"id": "ME_L4", "title": "Marketing Executive", "modules": [
            {"id": "a", "title": "A", "order": 0, "active": True, "selectable": True, "isMandatory": True},
            {"id": "b", "title": "B", "order": 1, "active": True, "selectable": True, "isMandatory": False}], "pathways": []}]}
        self.catalogues = {"ME_L4": {"records": [
            {"id": "ksb-1", "code": "K1", "kind": "Knowledge", "description": "One", "moduleKeys": ["a", "b"]},
            {"id": "ksb-2", "code": "S1", "kind": "Skill", "description": "Two", "moduleKey": "b"}]}}

    def test_required_optional_and_shared_ksb_are_unique(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        programme = snapshot["programmes"][0]
        self.assertEqual({k["code"] for k in applicable_ksbs(programme, ["a", "b"])}, {"K1", "S1"})
        self.assertEqual(validate_selection(programme, ["a", "b"]), ["a", "b"])
        with self.assertRaises(ValueError): validate_selection(programme, [])

    def test_version_is_deterministic_and_relationship_order_irrelevant(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        first = version(snapshot)
        snapshot["programmes"][0]["mappings"].reverse()
        self.assertEqual(first, version(snapshot))

    def test_projection_preserves_many_to_many_legacy_shape(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        record = project_catalogues(snapshot)["ME_L4"]["records"][0]
        self.assertEqual(record["moduleKeys"], ["a", "b"])

    def test_legacy_resolver_reports_compatibility_source(self):
        from .programme_structure import resolve_published
        result = resolve_published({"programmeModules": self.modules, "skillsRadarCatalogues": self.catalogues})
        self.assertEqual(result["source"], "legacy")
        self.assertIn("programmeModules projection differs", result["differences"])

    def test_canonical_resolver_does_not_fallback_or_mutate(self):
        from .programme_structure import resolve_published
        canonical = from_legacy(self.modules, self.catalogues)
        result = resolve_published({"programmeStructure": canonical, "programmeModules": {"programmes": []}})
        self.assertEqual(result["source"], "canonical")
        self.assertEqual(result["snapshot"]["version"], canonical["version"])

    def test_validation_rejects_duplicate_relationship_and_code(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        snapshot["programmes"][0]["mappings"].append(dict(snapshot["programmes"][0]["mappings"][0]))
        with self.assertRaises(ValueError): validate_structure(snapshot)

    def test_validation_rejects_unknown_mapping_reference(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        snapshot["programmes"][0]["mappings"][0]["moduleId"] = "missing"
        with self.assertRaises(ValueError): validate_structure(snapshot)

    def test_explicit_layout_version_never_falls_back_to_latest(self):
        from .programme_structure import resolve_explicit_layout
        with self.assertRaises(LookupError): resolve_explicit_layout({"programmeModules": self.modules}, 9, layout_version=10)
        self.assertEqual(resolve_explicit_layout({"programmeModules": self.modules, "skillsRadarCatalogues": self.catalogues}, 10, layout_version=10)["source"], "legacy")

    def test_legacy_projection_seeds_editable_feb_2027_cohort_without_changing_programme_modules(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        programme = snapshot["programmes"][0]
        self.assertEqual(programme["cohorts"][0]["id"], "ME_L4-FEB-2027")
        self.assertEqual(programme["cohorts"][0]["name"], "Feb 2027")
        self.assertEqual([m["id"] for m in programme["cohorts"][0]["modules"]], ["a", "b"])
        self.assertEqual([m["id"] for m in programme["modules"]], ["a", "b"])

    def test_cohort_modules_and_mappings_validate_independently(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        cohort = snapshot["programmes"][0]["cohorts"][0]
        cohort["modules"][0]["id"] = "cohort-a"
        cohort["mappings"] = [{"ksbId": "ksb-1", "moduleId": "cohort-a", "cohortModuleId": "cohort-a", "status": "active"}]
        validate_structure(snapshot)
        cohort["mappings"][0]["moduleId"] = "missing"
        with self.assertRaises(ValueError): validate_structure(snapshot)

    def test_assigned_cohort_requires_stable_id_and_matching_programme(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        self.assertIsNotNone(resolve_assigned_cohort(snapshot, 'ME_L4', 'ME_L4-FEB-2027'))
        self.assertIsNone(resolve_assigned_cohort(snapshot, 'MM_L6', 'ME_L4-FEB-2027'))
        self.assertIsNone(resolve_assigned_cohort(snapshot, 'ME_L4', 'Feb 2027'))

    def test_effective_learner_layout_keeps_public_four_field_contract(self):
        from enrolment_api import wizard_layout
        cursor = Mock()
        cursor.fetchone.return_value = (7, {'steps': []}, 'created', 'admin', 'default', None, None)
        result = wizard_layout.effective_layout(cursor, 'apprenticeship', 1601)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0], 7)

    def test_required_optional_and_shared_mapping_parity_fixture(self):
        from .programme_structure import project_modules, project_catalogues
        snapshot = from_legacy(self.modules, self.catalogues)
        projected = project_modules(snapshot); cats = project_catalogues(snapshot)
        self.assertEqual(projected["programmes"][0]["modules"][0]["id"], "a")
        self.assertEqual(cats["ME_L4"]["records"][0]["moduleKeys"], ["a", "b"])

    def test_four_programme_shadow_projection_inventory(self):
        from .modules_configuration import defaults
        from enrolment_api.skills_radar.catalogue import initial, PROGRAMMES
        from .programme_structure import from_legacy, project_catalogues
        catalogues = {pid: initial(pid) for pid in PROGRAMMES}
        snapshot = from_legacy(defaults(), catalogues)
        projected = project_catalogues(snapshot)
        self.assertEqual({p["id"] for p in snapshot["programmes"]}, set(PROGRAMMES))
        self.shadow_projection_counts = {pid: (len(catalogues[pid]["records"]), len(projected[pid]["records"])) for pid in PROGRAMMES}
        self.assertEqual(set(projected), set(PROGRAMMES))

    def test_semantic_parity_reports_real_standard_differences(self):
        from .modules_configuration import defaults
        from enrolment_api.skills_radar.catalogue import initial, PROGRAMMES
        snapshot = from_legacy(defaults(), {pid: initial(pid) for pid in PROGRAMMES})
        results = {pid: semantic_parity(defaults(), initial(pid), snapshot, pid) for pid in ('ME_L4', 'MM_L6', 'APM_L4')}
        self.assertTrue(all(isinstance(value, list) for value in results.values()))
        self.assertEqual(results['ME_L4'], [])

    def test_pcp_shadow_delegates_owner_pathway_metadata(self):
        from .modules_configuration import defaults
        from enrolment_api.skills_radar.catalogue import initial, PROGRAMMES
        snapshot = from_legacy(defaults(), {pid: initial(pid) for pid in PROGRAMMES})
        result = resolve_pcp_shadow(snapshot)
        self.assertEqual(result['mode'], 'legacy-pcp-allocation')
        self.assertTrue(result['pathways'])
        self.assertEqual(len(result['ksbs']), 71)

    def test_shadow_version_context_scenarios(self):
        self.assertEqual(shadow_version_context(layout_version='v1', available_versions=['v1'])['status'], 'consistent')
        self.assertEqual(shadow_version_context(layout_version='v1', skills_radar_versions={'ME_L4':'v2'}, available_versions=['v1','v2'])['status'], 'conflict')
        self.assertEqual(shadow_version_context(layout_version='v9', available_versions=['v1'])['status'], 'missing')

    def test_independent_version_numbers_use_source_identity(self):
        result = shadow_version_context(layout_version=7, programme_modules_version=7,
                                        skills_radar_versions={'ME_L4': 7},
                                        source_identities={'layout': 'published:42', 'modules': 'published:42', 'radar': 'published:42'})
        self.assertEqual(result['status'], 'consistent')
        result = shadow_version_context(layout_version=7, programme_modules_version=7,
                                        skills_radar_versions={'ME_L4': 7},
                                        source_identities={'layout': 'published:42', 'modules': 'published:43', 'radar': 'published:42'})
        self.assertEqual(result['status'], 'conflict')

    def test_pcp_shadow_returns_exact_legacy_selection_for_each_pathway(self):
        legacy = defaults()
        snapshot = from_legacy(legacy, {})
        for path in legacy['programmes'][3]['pathways']:
            modules = available_modules(path['modules'])
            mandatory = [m['id'] for m in modules if m.get('isMandatory')]
            optional = [m['id'] for m in modules if not m.get('isMandatory')][:path['requiredOptionalModulesCount']]
            raw = {'programme': 'PCP_L6', 'pathway': path['id'], 'configurationVersion': legacy['schemaVersion'],
                   'modulesByPathway': {path['id']: mandatory + optional}}
            expected = validate_snapshot_choices(raw, legacy['schemaVersion'], legacy, complete=False)
            actual = validate_pcp_shadow_selection(snapshot, raw, version=legacy['schemaVersion'])
            self.assertEqual(actual, expected)

    def test_shadow_learner_report_is_read_only_and_deduplicates_ksbs(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        before = repr(snapshot)
        report = shadow_compare_learner(snapshot, 'ME_L4', ['a', 'b'], layout_version='layout-1', module_version='modules-1', catalogue_version='catalogue-1',)
        self.assertIn(report['status'], {'MATCH', 'BLOCKED'})
        self.assertEqual(repr(snapshot), before)

    def test_standard_shadow_match_with_complete_published_identity(self):
        snapshot = from_legacy(self.modules, self.catalogues)
        report = shadow_compare_learner({'programmeStructure': snapshot}, 'ME_L4', ['a', 'b'], layout_version=12)
        self.assertEqual(report['status'], 'MATCH')
        self.assertEqual(report['ksbCount'], 2)
        self.assertEqual(len(report['ksbIds']), 2)

    def test_shadow_report_blocks_pcp_without_fabricating_direct_coverage(self):
        snapshot = from_legacy({'programmes': [{'id': 'PCP_L6', 'title': 'PCP', 'modules': [], 'pathways': []}]}, {})
        report = shadow_compare_learner(snapshot, 'PCP_L6')
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('reasons', report)

    def test_pcp_shadow_keeps_legacy_engine_delegation(self):
        from .modules_configuration import defaults
        from enrolment_api.skills_radar.catalogue import initial, PROGRAMMES
        snapshot = from_legacy(defaults(), {pid: initial(pid) for pid in PROGRAMMES})
        result = resolve_pcp_shadow(snapshot)
        self.assertEqual(result['mode'], 'legacy-pcp-allocation')
        self.assertEqual({p['id'] for p in result['pathways']}, {'operational','strategic','chartered'})

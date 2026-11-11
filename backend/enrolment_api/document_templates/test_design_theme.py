import copy
import uuid
from django.test import SimpleTestCase, TestCase
from . import test_templates as helpers, service, views
from .models import TemplateVersion
from .registry import seed, seeds, validate, legacy_equivalent, TemplateError, PROGRAMMES


class DocumentDesignSchemaTests(SimpleTestCase):
    def test_all_four_programme_variants_keep_their_unique_template_and_binding_metadata(self):
        for key in seeds()['templates']:
            for programme in PROGRAMMES:
                with self.subTest(key=key,programme=programme):
                    original=seed(key); original['templateId']+='@'+str(uuid.uuid4()); original['programmes']=[programme]
                    baseline=validate(original)
                    self.assertEqual(validate({**original,'designTheme':'lavender-v1'}),{**baseline,'designTheme':'lavender-v1'})
                    self.assertEqual(validate({**original,'designTheme':'purple-reference-v2'}),{**baseline,'designTheme':'purple-reference-v2'})

    def test_all_eight_design_settings_preserve_unique_blocks_bindings_and_programmes(self):
        for key in seeds()['templates']:
            with self.subTest(key=key):
                old=validate(seed(key)); themed=validate({**old,'designTheme':'lavender-v1'},publishing=True)
                self.assertEqual(themed,{**old,'designTheme':'lavender-v1'})
                self.assertTrue(legacy_equivalent(old)); self.assertFalse(legacy_equivalent(themed))
                self.assertTrue(legacy_equivalent(validate({**old,'designTheme':'classic'})))
                reference=validate({**old,'designTheme':'purple-reference-v2'},publishing=True)
                self.assertEqual(reference,{**old,'designTheme':'purple-reference-v2'})
                self.assertFalse(legacy_equivalent(reference))

    def test_supported_custom_formatting_is_retained_and_unknown_theme_is_rejected(self):
        content=seed('ilr'); content['designTheme']='lavender-v1'
        content['blocks'][0]['style']={'fontSize':14,'bold':False,'align':'right','spaceAfter':7}
        cleaned=validate(content)
        self.assertEqual(cleaned['blocks'][0]['style'],content['blocks'][0]['style'])
        for bad in ('unknown',{},[],None):
            with self.subTest(bad=bad),self.assertRaises(TemplateError):validate({**content,'designTheme':bad})


class DocumentDesignVersionTests(TestCase):
    databases={'default','enrolment'}
    setUp=helpers.TemplateWorkflowTests.setUp
    request=helpers.TemplateWorkflowTests.request
    payload=helpers.TemplateWorkflowTests.payload
    save=helpers.TemplateWorkflowTests.save
    publish=helpers.TemplateWorkflowTests.publish

    def test_save_theme_does_not_publish_and_publishing_keeps_old_issued_assignment(self):
        from learner_api.models import ApprenticeshipAgreement
        key='apprenticeship-agreement'; old=self.publish(seed(key))
        doc=ApprenticeshipAgreement.objects.create(learner_kind='apprenticeship',learner_id=self.learner.pk,apprentice_name='Historical learner')
        issue=service.bind_issue(key,doc,self.learner,{'particulars':{'apprenticeName':'Historical learner'},'signatures':{}})
        before=copy.deepcopy(issue.template_snapshot)
        content={**seed(key),'designTheme':'purple-reference-v2'}
        saved=self.save(content)
        self.assertEqual(saved['content']['designTheme'],'purple-reference-v2')
        self.assertEqual(service.latest(key)[0]['id'],old['id'])
        preview=self.payload(self.request(views.preview,'POST',{'revision':saved['revision']},identifier=key))
        self.assertEqual(preview['template']['content']['designTheme'],'purple-reference-v2')
        published=self.payload(self.request(views.publish,'POST',{'revision':saved['revision'],'previewToken':preview['previewToken'],'reviewConfirmed':True,'approvalNote':'Style only'},identifier=key),201)['published']
        self.assertEqual(published['number'],2)
        issue.refresh_from_db(); self.assertEqual(issue.template_snapshot,before)
        self.assertEqual(str(issue.version_id),old['id'])
        self.assertEqual(TemplateVersion.objects.get(pk=old['id']).content,validate(seed(key)))

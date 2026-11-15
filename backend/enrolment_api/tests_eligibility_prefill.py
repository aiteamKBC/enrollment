"""New provenance/security/prefill boundaries; all persistence/mail mocked."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from enrolment_api import eligibility_invitation as invite, eligibility_link as links, eligibility_prefill as prefill
from enrolment_api.tests_eligibility_invitation import ROW
from login import services


class PrefillTests(SimpleTestCase):
    def test_explicit_equivalences_exclude_programme_consent_signature_and_workplace_guessing(self):
        row={**ROW, 'id':41, 'job_title':'Analyst', 'country_of_residence':'France', 'nationality':'France',
             'representative_name':'Representative', 'representative_email':'rep@example.invalid',
             'main_workplace_postcode':'ME14 1XQ', 'main_workplace_country':'England',
             'workplace_arrangement':'different', 'employer_postcode':'SA1 1AD', 'years_in_uk':'5 or more'}
        with patch.object(prefill,'verified_application',return_value=row):
            result=prefill.defaults(812)
        self.assertEqual(result['draft']['ilrDetails']['jobTitle'],'Analyst')
        self.assertEqual(result['answers']['employer']['postcode'],'SA1 1AD')
        self.assertEqual(result['answers']['employer']['address'],'2 Business Park')
        self.assertNotIn('city',result['answers']['employer'])
        self.assertNotIn('lineManagerName',result['answers']['employer'])
        self.assertEqual(result['employerRepresentative']['name'],'Representative')
        self.assertNotIn('declarations',result['answers'])
        self.assertNotIn('learnerSignature',result['answers'])
        self.assertNotIn('yearsInUk',result['answers']['eligibility']) # Never turn "5 or more" into exactly five.
        self.assertFalse(any('programme' in key.lower() or 'signature' in key.lower() for key in prefill.DRAFT_FIELDS | prefill.ANSWER_FIELDS))

    def test_unlinked_or_unverified_records_have_no_defaults(self):
        with patch.object(prefill,'verified_application',return_value=None):
            self.assertIsNone(prefill.defaults(812))

    def test_first_login_identity_corrections_override_older_source(self):
        row={**ROW, 'id':41, '_learner':{'Phone_number':'new phone', 'Date_of_birth':'1990-02-03',
             'Current_address_line_1':'Corrected street', 'Country':'France', 'Current_postcode':'75001'}}
        with patch.object(prefill,'verified_application',return_value=row):
            result=prefill.defaults(812)
        self.assertEqual(result['draft']['personalDetails']['phone'],'new phone')
        self.assertEqual(result['draft']['personalDetails']['address'],'Corrected street')
        self.assertEqual(result['answers']['eligibility']['countryOfResidence'],'France')
        self.assertEqual(result['draft']['ilrDetails']['postcodePriorToEnrolment'],'75001')

    def test_eligibility_title_is_not_prefilled_but_profile_title_is_preserved(self):
        row = {**ROW, 'id': 41, 'title': 'Dr', '_learner': {'Title': 'Ms'}}
        with patch.object(prefill, 'verified_application', return_value=row):
            result = prefill.defaults(812)
        self.assertEqual(result['draft']['personalDetails']['title'], 'Ms')
        row['_learner'] = {}
        with patch.object(prefill, 'verified_application', return_value=row):
            result = prefill.defaults(812)
        self.assertNotIn('title', result['draft'].get('personalDetails', {}))

    def test_commercial_reads_do_not_query_eligibility(self):
        with patch.object(prefill,'defaults') as source:
            self.assertEqual(prefill.add_defaults({'answers':None},'commercial',812),{'answers':None})
        source.assert_not_called()

    def test_creation_mapping_uses_four_residential_lines_and_no_target_programme(self):
        row={**ROW, 'residential_address_line1':'Street', 'residential_address_line2':'Flat',
             'residential_town_city':'Paris','residential_county':'France','country_of_residence':'France'}
        result=invite.learner_fields(row)
        self.assertEqual([result[f'address_line_{n}'] for n in range(1,5)],['Street','Flat','Paris','France'])
        self.assertNotIn('target_programme',result)
        self.assertNotIn('title',result)

    def test_company_address_uses_active_lines_one_two_four_and_separate_city(self):
        row={**ROW, 'id':41, 'workplace_arrangement':'same', 'employer_postcode':'ME14 1XQ',
             'employer_postcode_country':'England', 'employer_address_line1':'2 Work Road',
             'employer_address_line2':'Building B', 'employer_address_line3':'Historical additional details',
             'employer_town_city':'Maidstone', 'employer_county':'Kent, England',
             'main_workplace_postcode':'SA1 1AD'}
        with patch.object(prefill,'verified_application',return_value=row):
            employer=prefill.defaults(812)['answers']['employer']
        self.assertEqual(employer,{'organisationName':'Example Ltd','postcode':'ME14 1XQ',
            'address':'2 Work Road, Building B, Kent, England','city':'Maidstone'})

    def test_legacy_full_company_address_is_not_split_or_overwritten(self):
        row={**ROW, 'workplace_arrangement':'same', 'employer_postcode':'ME14 1XQ','employer_postcode_country':'England'}
        self.assertEqual(prefill.company_employer_values(row),{'postcode':'ME14 1XQ','address':'2 Business Park'})

    def test_every_arrangement_keeps_company_information_and_excludes_main_workplace(self):
        row={**ROW, 'workplace_arrangement':'remote_hybrid_multiple','employer_postcode':'SA1 1AD',
             'employer_postcode_country':'Wales','main_workplace_postcode':'ME14 1XQ','main_workplace_country':'England'}
        expected={'postcode':'SA1 1AD','address':'2 Business Park'}
        for arrangement in ('same','different','remote_hybrid_multiple',None):
            with self.subTest(arrangement=arrangement):
                self.assertEqual(prefill.company_employer_values({**row,'workplace_arrangement':arrangement}),expected)
        self.assertEqual(prefill.company_employer_values({**row,'main_workplace_postcode':None}),expected)

    def test_missing_company_postcode_never_falls_back_to_main_workplace(self):
        self.assertEqual(prefill.company_employer_values({'main_workplace_postcode':'ME14 1XQ','main_workplace_country':'England'}),{})

    def test_postcode_geography_is_never_used_to_invent_company_address_or_city(self):
        self.assertEqual(prefill.company_employer_values({'employer_postcode':'ME14 1XQ',
            'employer_postcode_district':'Maidstone','employer_postcode_country':'England'}),{'postcode':'ME14 1XQ'})

    def test_all_verified_uk_countries_are_valid_prefill_evidence(self):
        for country in ('England','Wales','Scotland','Northern Ireland'):
            with self.subTest(country=country):
                self.assertEqual(prefill.company_employer_values({'workplace_arrangement':'different',
                    'employer_postcode':'COMPANY','employer_postcode_country':country,
                    'main_workplace_postcode':'WORKPLACE','main_workplace_country':'England'}),{'postcode':'COMPANY'})


class LinkedInvitationTests(SimpleTestCase):
    def test_account_and_link_are_created_together_but_mail_waits_for_commit(self):
        callbacks=[]
        learners=MagicMock()
        learners.create.return_value=SimpleNamespace(pk=812)
        fake_transaction=SimpleNamespace(atomic=lambda **_:nullcontext(),on_commit=lambda action,**_:callbacks.append(action))
        with patch.object(invite,'has_account',return_value=False), patch.object(invite,'transaction',fake_transaction), \
             patch('learner_api.models.EnrolmentUser.all_learners',learners), \
             patch('login.identity.ensure_account',return_value=(SimpleNamespace(subject_type='learner',subject_id=812),True)), \
             patch.object(links,'add_link') as link, patch.object(links,'dispatch',return_value='invited') as deliver:
            self.assertEqual(invite.invite_applicant(ROW,application_id=41),'invitePending')
            link.assert_called_once_with(41,812)
            deliver.assert_not_called()
            self.assertEqual(len(callbacks),1)
            callbacks[0]()
            deliver.assert_called_once_with(41,ip=None,user_agent=None)

    def test_existing_email_is_not_claimed_or_updated(self):
        with patch.object(invite,'has_account',return_value=True), patch.object(links,'add_link') as link, patch('login.identity.ensure_account') as account:
            self.assertEqual(invite.invite_applicant(ROW,application_id=41),'existingAccount')
        link.assert_not_called()
        account.assert_not_called()

    def test_source_link_blocks_shared_default_password_but_unlinked_legacy_flow_is_preserved(self):
        account=SimpleNamespace(subject_type='learner',subject_id=812,has_password=False,is_active=True)
        with patch('login.identity.account_for_email',return_value=account), patch('login.security.is_locked',return_value=False), \
             patch.object(links,'link_for_learner',return_value=(41,None)) as link, \
             patch.object(services,'_delivered_invitation',return_value=(object(),'SYNTHETIC')) as issue:
            self.assertIsNone(services.default_password_setup('test@example.invalid'))
            issue.assert_not_called()
            link.return_value=None
            self.assertTrue(services.default_password_setup('test@example.invalid').startswith('/set-password?token='))
            issue.assert_called_once()

    def test_claim_loser_does_not_send_a_second_invitation(self):
        cursor=MagicMock()
        cursor.__enter__.return_value=cursor
        cursor.fetchone.return_value=None
        connection=SimpleNamespace(in_atomic_block=False,cursor=lambda:cursor)
        with patch.object(links,'connections',{'enrolment':connection}), patch.object(links.transaction,'atomic',lambda **_:nullcontext()), patch('login.services.invite_eligible_applicant') as mail:
            self.assertEqual(links.dispatch(41),'invitePending')
        mail.assert_not_called()

import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from contextlib import nullcontext
from enrolment_api import programme_structure_views as views
class Store:
 row=None
 @classmethod
 def filter(cls,**kw): return SimpleNamespace(first=lambda: cls.row, delete=lambda: setattr(cls,'row',None))
 @classmethod
 def update_or_create(cls,**kw): cls.row=SimpleNamespace(configuration=kw['defaults']['configuration'],base_version=kw['defaults']['base_version']); return cls.row,True
class PreviewCursor:
 def __enter__(self): return self
 def __exit__(self,*args): return None
class PreviewConnection:
 def cursor(self): return PreviewCursor()
class HttpDraftTests(TestCase):
 def setUp(self): self.f=RequestFactory(); Store.row=None; self.s={'programmes':[{'id':'ME_L4','title':'ME','modules':[{'id':'m1','title':'M','selection':'required'}],'ksbs':[{'id':'k1','code':'K1','kind':'Knowledge','description':'D','status':'active'}],'mappings':[{'programmeId':'ME_L4','ksbId':'k1','moduleId':'m1','status':'active'}]}]}
 def call(self,view,method='get',body=None,actor=1,staff=True):
  r=getattr(self.f,method)('/' if method != 'get' else '/', data=json.dumps(body or {}) if method != 'get' else None, content_type='application/json'); r.login_account=SimpleNamespace(pk=actor); r.user=SimpleNamespace(pk=actor)
  with patch.object(views,'is_staff',return_value=staff),(nullcontext() if view.__name__ == 'programme_structure_draft' else patch.object(views.ModuleConfigurationDraft,'objects',Store)),patch('enrolment_api.wizard_layout.read_current',return_value=(4, {'programmeStructure': self.s})),patch.object(views,'connections',{'enrolment': PreviewConnection()}):
   fn=view
   while hasattr(fn,'__wrapped__'): fn=fn.__wrapped__
   return fn(r)
 def test_get_put_delete_lifecycle_and_revision(self):
  self.assertEqual(self.call(views.programme_structure_draft).status_code,200); x=self.call(views.programme_structure_draft,'put',{'structure':self.s,'revision':0}); self.assertEqual(x.status_code,200); self.assertEqual(json.loads(x.content)['revision'],1); self.assertEqual(self.call(views.programme_structure_draft,'delete',{'revision':1}).status_code,200)
 def test_stale_revision_conflict_preserves_store(self):
  self.call(views.programme_structure_draft,'put',{'structure':self.s,'revision':0}); changed=json.loads(json.dumps(self.s)); changed['programmes'][0]['title']='Other'; x=self.call(views.programme_structure_draft,'put',{'structure':changed,'revision':0}); self.assertEqual(x.status_code,409); self.assertEqual(views.ModuleConfigurationDraft.objects.get(pk=views.SHARED_DRAFT_KEY).configuration['programmes'][0]['title'],'ME')
 def test_validation_read_only_and_permission(self):
  x=self.call(views.programme_structure_validate,'post',{'structure':self.s}); self.assertEqual(x.status_code,200); self.assertIsNone(Store.row); self.assertEqual(self.call(views.programme_structure_validate,'post',{'structure':self.s},staff=False).status_code,403)
 def test_invalid_relationship_rejected(self):
  bad=json.loads(json.dumps(self.s)); bad['programmes'][0]['mappings'][0]['moduleId']='missing'; self.assertEqual(self.call(views.programme_structure_draft,'put',{'structure':bad,'revision':0}).status_code,400)

 def test_preview_is_read_only_deterministic_and_rejects_unsupported_programme(self):
  first = self.call(views.programme_structure_preview, 'post', {'structure': self.s})
  second = self.call(views.programme_structure_preview, 'post', {'structure': self.s})
  self.assertEqual(first.status_code, 200); self.assertEqual(json.loads(first.content)['version'], json.loads(second.content)['version']); self.assertIsNone(Store.row)
  unsupported = json.loads(json.dumps(self.s)); unsupported['programmes'][0]['id'] = 'NEW_L3'
  result = self.call(views.programme_structure_preview, 'post', {'structure': unsupported})
  self.assertEqual(result.status_code, 200); self.assertEqual(json.loads(result.content)['valid'], False)

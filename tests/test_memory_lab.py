import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from memory_lab.evidence import load_evidence, ValidationError
from memory_lab.audit import audit
from memory_lab.state import replay
from memory_lab.adapters import CallableExtractor
ROOT=Path(__file__).resolve().parents[1]
class Tests(unittest.TestCase):
 def setUp(self):
  self.d=json.loads((ROOT/'examples/demo.json').read_text()); self.e=load_evidence(self.d['evidence'],ROOT/'examples')
 def replay(self,events=None,**kw):
  return replay(self.d['events'] if events is None else events,self.d['policy'],self.e,**kw)
 def audit(self,a=None):
  return audit(self.d['memories'],self.d['assertions'] if a is None else a,self.e)
 def test_demo(self):
  s=self.replay()['states']['A']; self.assertEqual(s['status'],'rejected'); self.assertTrue(s['reconsideration_requested']); self.assertEqual(s['dependencies'],['B']); self.assertEqual(s['last_authorized_decision']['event_id'],'event-wed'); self.assertEqual(s['pending'],['product_manager_review'])
 def test_unauthorized(self):
  e=dict(self.d['events'][-1],id='fake',action='approve'); o=self.replay(self.d['events']+[e]); self.assertEqual(o['states']['A']['status'],'rejected'); self.assertEqual(o['audit_log'][-1]['outcome'],'unauthorized')
 def test_unknown_actor(self):
  e=dict(self.d['events'][2],actor='impostor'); self.assertEqual(self.replay([e])['audit_log'][0]['outcome'],'unauthorized')
 def test_approval(self):
  e=dict(self.d['events'][-1],id='approve',actor='pm',action='approve'); s=self.replay(self.d['events']+[e])['states']['A']; self.assertEqual(s['status'],'approved'); self.assertFalse(s['reconsideration_requested']); self.assertEqual(s['pending'],[])
 def test_cutoff(self):
  s=self.replay(as_of='2026-10-06T23:59:59Z')['states']['A']; self.assertEqual(s['status'],'unresolved'); self.assertEqual(s['dependencies'],['B']); self.assertIsNone(s['last_authorized_decision'])
 def test_sort(self):
  self.assertEqual(self.replay(),self.replay(list(reversed(self.d['events']))))
 def test_tie(self):
  reject=self.d['events'][2]; approve=dict(reject,id='approve',action='approve'); self.assertEqual(self.replay([reject,approve])['states']['A']['status'],'approved'); self.assertEqual(self.replay([approve,reject])['states']['A']['status'],'rejected')
 def test_revoke(self):
  e=dict(self.d['events'][-1],id='revoke',actor='pm',action='revoke',supersedes='wrong'); o=self.replay(self.d['events']+[e]); self.assertEqual(o['audit_log'][-1]['outcome'],'invalid_supersession'); self.assertEqual(o['states']['A']['status'],'rejected'); e['supersedes']='event-wed'; self.assertEqual(self.replay(self.d['events']+[e])['states']['A']['status'],'unresolved')
 def test_duplicate(self):
  with self.assertRaises(ValidationError): self.replay(self.d['events']*2)
 def test_naive_time(self):
  with self.assertRaises(ValidationError): self.replay(as_of='2026-10-06')
 def test_unknown_action(self):
  with self.assertRaises(ValidationError): self.replay([dict(self.d['events'][0],action='invent')])
 def test_tamper(self):
  with tempfile.TemporaryDirectory() as tmp:
   for p in (ROOT/'examples').glob('*.txt'): (Path(tmp)/p.name).write_bytes(p.read_bytes())
   (Path(tmp)/'wed.txt').write_text('Product manager approves A.')
   with self.assertRaises(ValidationError): load_evidence(self.d['evidence'],tmp)
 def test_invented_quote(self):
  records=copy.deepcopy(self.d['evidence']); records[0]['quote']='invented'
  with self.assertRaises(ValidationError): load_evidence(records,ROOT/'examples')
 def test_escape(self):
  records=copy.deepcopy(self.d['evidence']); records[0]['path']='../README.md'
  with self.assertRaises(ValidationError): load_evidence(records,ROOT/'examples')
 def test_provenance_required(self):
  records=copy.deepcopy(self.d['evidence']); del records[0]['source_uri']
  with self.assertRaises(ValidationError): load_evidence(records,ROOT/'examples')
 def test_symlink_escape(self):
  with tempfile.TemporaryDirectory() as tmp:
   (Path(tmp)/'mon.txt').symlink_to(ROOT/'examples/mon.txt')
   with self.assertRaises(ValidationError): load_evidence([self.d['evidence'][0]],tmp)
 def test_missing_reference(self):
  with self.assertRaises(ValidationError): self.audit([{'memory_id':'m1','relation':'supports','evidence_ids':['fabricated']}])
 def test_findings(self):
  f=self.audit()['findings']; self.assertEqual([x['status'] for x in f],['challenged','supported','insufficient_evidence']); self.assertEqual(f[0]['evidence'][0]['sha256'],self.e['wed']['sha256']); self.assertIsNone(f[0]['revision_proposal']['replacement'])
 def test_conflict(self):
  a=self.d['assertions']+[{'memory_id':'m1','relation':'supports','evidence_ids':['thu']}]; self.assertEqual(self.audit(a)['findings'][0]['status'],'conflicted')
 def test_adapter(self):
  self.assertTrue(CallableExtractor(lambda e:self.d['assertions']).extract(self.e)['requires_human_review'])
  with self.assertRaises(ValidationError): CallableExtractor(lambda e:[{'relation':'supports','evidence_ids':['fake']}]).extract(self.e)
 def test_no_mutation(self):
  before=copy.deepcopy(self.d); self.replay(); self.audit(); self.assertEqual(before,self.d)
 def test_cli(self):
  files={p:p.read_bytes() for p in (ROOT/'examples').iterdir()}; r=subprocess.run([sys.executable,'-m','memory_lab','demo','examples/demo.json'],cwd=ROOT,capture_output=True,text=True); self.assertEqual(r.returncode,0,r.stderr); self.assertEqual(json.loads(r.stdout)['organizational_memory']['states']['A']['status'],'rejected'); self.assertEqual(files,{p:p.read_bytes() for p in files})
 def test_bad_cli(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'bad.json'; p.write_text('{}'); r=subprocess.run([sys.executable,'-m','memory_lab','audit',str(p)],cwd=ROOT,capture_output=True,text=True); self.assertEqual(r.returncode,2); self.assertNotIn('Traceback',r.stderr)
if __name__=='__main__': unittest.main()

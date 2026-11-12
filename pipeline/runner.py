import pathlib,sys,json,os,time,hashlib,threading,fcntl,random,concurrent.futures,collections,contextlib
REPO=pathlib.Path(__file__).resolve().parent.parent
R=pathlib.Path(os.environ.get('BIO_QA_WORKSPACE', pathlib.Path.cwd())).resolve()
sys.path[:0]=[str(R/'extras'),str(R/'deps'),str(REPO/'frozen/pipeline'),str(REPO/'pipeline'),str(REPO)]
import run_workflow_v5 as pipeline
from client import Client
from formatting import format_document,validate,identifier_set
from lexical_cache import LexicalCache
from budget_guard import get_budget,RATES
load=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
class Admission:
 def __init__(self,n,rpm):self.sem=threading.BoundedSemaphore(n);self.lock=threading.Lock();self.spacing=60/rpm;self.next=0;self.active=0;self.peak=0;self.starts=0;self.stopped=threading.Event()
 def __enter__(self):
  self.sem.acquire()
  if self.stopped.is_set():self.sem.release();raise RuntimeError('admission_stopped')
  with self.lock:wait=max(0,self.next-time.monotonic());self.next=max(self.next,time.monotonic())+self.spacing
  if wait:self.stopped.wait(wait)
  if self.stopped.is_set():self.sem.release();raise RuntimeError('admission_stopped')
  with self.lock:self.active+=1;self.starts+=1;self.peak=max(self.peak,self.active)
 def __exit__(self,*a):
  with self.lock:self.active-=1
  self.sem.release()
 def summary(self):
  with self.lock:return {'active':self.active,'peak':self.peak,'admissions':self.starts}
class SafeClient(Client):
 def call(self,stage,model,prompt,max_tokens,extra_body=None):
  folder=self.root/'runs'/stage
  # Never replay ambiguous dispatches or terminal unsuccessful requests.
  for pending in folder.glob('attempt_*.request.json'):
   if not pending.with_name(pending.name.replace('.request','')).exists():raise RuntimeError('unknown_dispatch_hold')
  if (folder/'attempt_1.json').exists():return super().call(stage,model,prompt,max_tokens,extra_body)
  with admission:
   result=super().call(stage,model,prompt,max_tokens,extra_body)
   if __import__('network_guard').blocked(self.base) or result.get('http_status') in [401,402,403,429] or result.get('account_blocked') or 'key_limited' in str(result.get('error','')):admission.stopped.set()
   return result
def strict_refs(q,bs):
 text=q['question']+'\n'+q['answer'];assert not __import__('re').search(r'\bdocument:',text,__import__('re').I),'document placeholder remains'
 known={b['id'] for b in bs}
 for ref in __import__('re').findall(r'\b\w+:b\d+\b',text):assert ref in known,'Unknown literal citation'
def main(key=None):
 global admission
 lock=(R/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 cfg=load(R/'config.json');key=key or sys.stdin.readline().strip();assert key;start=time.time()
 for required in ['schema.json','node_types.json','edge_types.json','pipeline/prompts.json','pipeline/lexical_seed.json','tokenizer/tokenizer.json','tokenizer/chat_template.jinja']:assert (R/'frozen'/required).is_file(),'Missing frozen artifact: '+required
 os.environ.update(BIO_API_ROUTE='direct',BIO_API_STREAM_MODELS='',BIO_BATCH_BUDGET_DB=str(R/'budget.sqlite'),BIO_BATCH_BUDGET_CNY=str(cfg['budget_cny']))
 for k in ['BIO_API_STREAM','BIO_ACCOUNT_RECOVERY']:os.environ.pop(k,None)
 RATES['claude-sonnet-5-5']=(14.4,57.6,1.152);pipeline.MODELS=cfg['models'];prompts=load(R/'frozen/pipeline/prompts.json')
 admission=Admission(cfg['api_concurrency'],cfg['requests_per_minute']);docs=load(R/'production_manifest.json');random.Random(cfg['seed']).shuffle(docs)
 fingerprint=hashlib.sha256(json.dumps({'config':cfg,'inputs':load(R/'production_input_hashes.json'),'prompts':prompts},sort_keys=True).encode()).hexdigest()
 if (R/'status.json').exists():assert load(R/'status.json')['fingerprint']==fingerprint,'Changed config use new run'
 state={'status':'running','pid':os.getpid(),'fingerprint':fingerprint,'total':len(docs),'results':[],'budget_limit_cny':cfg['budget_cny'],'quality_sample_ids':[d['document_id'] for d in docs[:3]]};save(R/'status.json',state)
 def one(doc):
  d=doc['document_id'];out=R/'tasks'/d;p=out/'papers'/d;resultfile=out/'result.json'
  if resultfile.exists() and load(resultfile)['status'] not in ['budget_hold','admission_hold']:return {**load(resultfile),'reused_terminal':True}
  out.mkdir(parents=True,exist_ok=True);t=time.time();result={'document_id':d,'status':'technical_failure'}
  def finish():
   if any(x in str(result.get('error',''))+' '+str(result.get('science',{}).get('error','')) for x in ['admission_stopped','operator_admission_hold','Transport circuit open','Transport circuit opened','network_circuit']):result['status']='admission_hold'
   result['seconds']=time.time()-t
   if any(load(f).get('budget_blocked') for f in (out/'runs').rglob('blocked.json')) and result['status']!='ready':result['status']='budget_hold'
   return result
  save(out/'run_config.json',{'manifest':{'documents':[doc],'runtime':cfg['runtime']},'manifest_base':str(R),'models':pipeline.MODELS})
  try:
   c=SafeClient(out,key,cfg['api_base'],1,cfg['max_transport_attempts'],cfg['runtime']['deadline_seconds']);c.model_gates={};c.circuit_breakers=True
   science=pipeline.execute(doc,out,c,prompts,R,None,cfg['runtime']);result['science']=science
   if science['status']!='argon_pass':
    result['status']='scientific_reject' if science['status']=='argon_not_pass' else 'technical_or_structure_failure';return finish()
   bs=load(R/doc['sources']);g=load(p/'dag.json');q=load(p/'qa.json');strict_refs(q,bs)
   fmt=format_document(out,c,doc,LexicalCache(R/'frozen/pipeline/lexical_seed.json'));result['formatting']=fmt
   if fmt['status']!='ready':result['status']='formatting_failure';return finish()
   fq=load(p/'qa_formatted.json');proof=validate(q,fq,identifier_set(bs));pipeline.qa_check(fq,d,bs,g);strict_refs(fq,bs)
   if fq!=q:
    dest=p/'argon_final.json'
    if dest.exists():v=load(dest)
    else:
     rr=c.call(d+'/verify_final','gemini-4-argon',prompts['verify']+json.dumps(fq,ensure_ascii=False)+'\nORIGINAL SOURCE BLOCKS\n'+pipeline.source(bs),6500)
     assert isinstance(rr.get('parsed'),dict) and not rr.get('provider_refusal'),rr.get('error','No final review');v=rr['parsed'];pipeline.review_check(v,bs);save(dest,v)
   else:dest=p/'argon.json';v=load(dest)
   pipeline.review_check(v,bs)
   result.update(status='ready' if v['verdict']=='pass' and not v['major_issues'] else 'scientific_reject_final',final_qa_sha256=sha(p/'qa_formatted.json'),final_review_sha256=sha(dest),final_review_path=str(dest.relative_to(R)),minor_issues=len(v['minor_issues']),source_sha256=sha(R/doc['sources']))
   save(p/'final_integrity.json',{'qa_sha256':result['final_qa_sha256'],'review_sha256':result['final_review_sha256'],'reviewed_formatted_version':True,'formatting_proof':proof})
  except Exception as e:result['error']=str(e).replace(key,'[REDACTED]')[:1200]
  return finish()

 def runone(doc):
  # one() persists terminal outcomes in a common wrapper; exceptions don't replay API.
  t=time.time()
  try:r=one(doc)
  except Exception as e:r={'document_id':doc['document_id'],'status':'technical_failure','error':str(e).replace(key,'[REDACTED]')[:1200]}
  r['seconds']=r.get('seconds',time.time()-t);save(R/'tasks'/doc['document_id']/'result.json',r);return r
 def update():
  state.update(elapsed_seconds=time.time()-start,finished=len(state['results']),outcomes=dict(collections.Counter(x['status'] for x in state['results'])),budget=get_budget().summary(),api=admission.summary());save(R/'status.json',state)
 canary_path=R/'resume_canary_ids.json'
 canary_ids=set(load(canary_path)) if canary_path.exists() else set()
 for group_index,group in enumerate([[d for d in docs if d['document_id'] in canary_ids],[d for d in docs if d['document_id'] not in canary_ids]]):
  state['phase']='canary' if group_index==0 else 'remaining';update()
  with concurrent.futures.ThreadPoolExecutor(max_workers=cfg['workers']) as pool:
   pending={pool.submit(runone,d) for d in group}
   while pending:
    done,pending=concurrent.futures.wait(pending,timeout=15,return_when=concurrent.futures.FIRST_COMPLETED)
    for f in done:state['results'].append(f.result())
    update()
  if group_index==0 and not all(x['status'] in ['ready','scientific_reject','scientific_reject_final'] for x in state['results']):state['status']='canary_blocked';update();return
 state['phase']='independent_measurement';update()
 def measure(d):
  out=R/'independent_review'/d;dest=out/'result.json'
  if dest.exists():return load(dest)
  paper=R/'tasks'/d/'papers'/d;qpath=paper/'qa_formatted.json'
  if not qpath.exists():qpath=paper/'qa.json'
  if not qpath.exists():r={'document_id':d,'status':'missing_qa'}
  else:
   try:
    doc=next(x for x in docs if x['document_id']==d);bs=load(R/doc['sources']);q=load(qpath);c=SafeClient(out,key,cfg['api_base'],1,1,900);c.model_gates={};c.circuit_breakers=True
    rr=c.call(d+'/independent_verify','gemini-4-argon',prompts['verify']+json.dumps(q,ensure_ascii=False)+'\nORIGINAL SOURCE BLOCKS\n'+pipeline.source(bs),6500)
    assert isinstance(rr.get('parsed'),dict) and not rr.get('provider_refusal'),rr.get('error','No review');v=rr['parsed'];pipeline.review_check(v,bs);save(out/'review.json',v);r={'document_id':d,'status':'reviewed','verdict':v['verdict'],'major_count':len(v['major_issues']),'qa_sha256':sha(qpath),'qa_path':str(qpath.relative_to(R))}
   except Exception as e:r={'document_id':d,'status':'measurement_failure','error':str(e).replace(key,'[REDACTED]')[:1200]}
  save(dest,r);return r
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:state['independent_quality']=list(pool.map(measure,state['quality_sample_ids']))
 assert all(sha(R/p)==h for p,h in load(R/'production_input_hashes.json').items());state['status']='finished';update()
 from finalize import finalize
 finalize()
if __name__=='__main__':main()

import argparse, concurrent.futures, graphlib, hashlib, json, os, pathlib, re, sys, time
from jsonschema import Draft202012Validator
from client import Client, ContentFiltered
ROOT=pathlib.Path(__file__).resolve().parent.parent
MODELS={'extract':'gpt-6-astra','generate':'claude-opus-5-5','verify':'gemini-4-argon'}
def load(p): return json.loads(pathlib.Path(p).read_text())
def save(p,x):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def source(bs):return '\n\n'.join('['+b['id']+'] '+b.get('section','')+'\n'+b['text'] for b in bs)
def graph_check(g,d,bs):
 Draft202012Validator(load(ROOT/'schema.json')).validate(g)
 assert g['document_id']==d and g['nodes'],'Empty or mismatched DAG'
 ids=[n['id'] for n in g['nodes']];known={b['id'] for b in bs};assert len(ids)==len(set(ids)),'Duplicate nodes'
 deps={i:[] for i in ids}
 for n in g['nodes']:
  assert n['id'].startswith(d+':') and set(n['evidence_ids'])<=known,'Invalid node reference'
  for q in n['quantities']:assert set(q['evidence_ids'])<=known,'Unknown quantity reference'
 for e in g['edges']:
  assert e['source'] in deps and e['target'] in deps and set(e['evidence_ids'])<=known,'Invalid edge'
  deps[e['target']].append(e['source'])
 return list(graphlib.TopologicalSorter(deps).static_order())
def normalize(q,d,bs,g=None):
 q=json.loads(json.dumps(q));known={b['id'] for b in bs}
 def fix(m):
  text=re.sub(r'\bdocument:(b\d{4})',lambda x:d+':'+x[1],m[0]);text=re.sub(r'(?<![\w:])(b\d{4})\b',lambda x:d+':'+x[1],text)
  refs=set(re.findall(r'\b\w+:b\d{4}\b',text));return text if refs and refs<=known else m[0]
 for k in ['question','answer']:q[k]=re.sub(r'\[[^\[\]\n]{1,300}\]',fix,q[k])
 def reference(value,allowed):
  if not isinstance(value,str):return value
  candidate=d+':'+value if ':' not in value else d+':'+value.split(':',1)[1] if value.startswith('document:') else value
  return candidate if candidate in allowed else value
 nodes={n['id'] for n in g['nodes']} if g else set()
 for step in q.get('reasoning_dependencies',[]) if isinstance(q.get('reasoning_dependencies'),list) else []:
  if not isinstance(step,dict):continue
  for field,allowed in [('evidence_ids',known),('premise_node_ids',nodes)]:
   if isinstance(step.get(field),list):step[field]=[reference(v,allowed) for v in step[field]]
 return q

def qa_check(q,d,bs,g):
 assert q.get('document_id')==d and all(isinstance(q.get(k),str) and q[k].strip() for k in ['question','answer']),'Invalid QA'
 known={b['id'] for b in bs};nodes={n['id'] for n in g['nodes']}
 refs=set(re.findall(r'\b\w+:b\d{4}\b',q['question']+'\n'+q['answer']))
 assert refs and refs<=known,'Unknown or absent QA citations'
 steps=q.get('reasoning_dependencies');assert isinstance(steps,list) and steps,'Missing dependencies'
 step_ids=[x['step_id'] for x in steps];assert len(set(step_ids))==len(step_ids),'Duplicate steps'
 deps={i:[] for i in step_ids}
 for s in steps:
  assert set(s.get('evidence_ids',[]))<=known and set(s.get('premise_node_ids',[]))<=nodes,'Unknown dependency reference'
  target=s.get('supports_step_id')
  if target is not None:assert target in deps,'Unknown target step';deps[target].append(s['step_id'])
 list(graphlib.TopologicalSorter(deps).static_order())

def review_check(v,bs):
 assert v.get('verdict') in ['pass','revise','fail'],'Invalid Argon verdict'
 for k in ['major_issues','minor_issues','formatting_issues']:assert isinstance(v.get(k),list),'Missing review field '+k
 for k in ['factual_grounding','reasoning_coherence','cross_section_dependency','scientific_depth','question_answer_alignment']:
  assert type(v.get('ratings',{}).get(k)) is int and 1<=v['ratings'][k]<=5,'Invalid rating'
 a=v.get('reasoning_assessment',{});assert a.get('classification') in ['connected_reasoning','mostly_summary','mixed'],'Missing reasoning assessment'
 known={b['id'] for b in bs}
 for issue in v['major_issues']+v['minor_issues']:assert set(issue.get('evidence_ids',[]))<=known,'Unknown review citation'
 assert set(a.get('essential_evidence_ids',[]))<=known,'Unknown review evidence'

def recovered_nodes(text):
 m=re.search(r'"nodes"\s*:\s*\[',text);assert m,'No recoverable nodes'
 pos=m.end();dec=json.JSONDecoder();nodes=[]
 while pos<len(text):
  while pos<len(text) and (text[pos].isspace() or text[pos]==','):pos+=1
  try:n,end=dec.raw_decode(text[pos:])
  except ValueError:break
  assert isinstance(n,dict) and 'id' in n;nodes.append(n);pos+=end
 assert nodes,'No complete nodes';return nodes

def selection_gate(doc,out,decisions):
 if decisions is None:return None
 decision=decisions[doc['document_id']]
 if decision['decision']=='keep':return None
 state={'document_id':doc['document_id'],'status':'screen_excluded' if decision['decision']=='exclude' else 'screen_held','origin':'live','screening_reason':decision['reason_zh']}
 save(out/'papers'/doc['document_id']/'state.json',state);return state

def execute(doc,out,client,prompts,base,baseline=None,runtime=None):
 runtime=runtime or {}
 d=doc['document_id'];bs=load(base/doc['sources']);dest=out/'papers'/d;fp=digest({'doc':doc,'blocks':bs,'prompts':prompts,'models':MODELS,'schema':load(ROOT/'schema.json'),'runtime':runtime}); checkpoint=dest/'checkpoint.json'
 if checkpoint.exists():assert load(checkpoint)['fingerprint']==fp,'Input/config changed; use a new output directory'
 else:save(checkpoint,{'fingerprint':fp})
 def stage(name,fn):
  p=dest/(name+'.json')
  if p.exists():return load(p)
  x=fn();save(p,x);return x
 try:
  def extract():
   if baseline:return load(baseline/'astra_typed_dag/raw_graphs'/f'{d}.json')
   prompt=prompts['extract']+json.dumps(load(ROOT/'node_types.json'),ensure_ascii=False)+'\nEDGE TYPES\n'+json.dumps(load(ROOT/'edge_types.json'),ensure_ascii=False)+'\nDOCUMENT='+d+'\nORIGINAL EVIDENCE\n'+source(bs)
   r=client.call(d+'/extract',MODELS['extract'],prompt,runtime.get('extract_max_tokens',28000))
   if r.get('finish_reason')=='length':
    ns=recovered_nodes(r['raw_text']);p=prompts['complete']+json.dumps(load(ROOT/'node_types.json'))+'\nSCHEMA\n'+json.dumps(load(ROOT/'schema.json'))+'\nDOCUMENT\n'+d+'\nCOMPLETE RECOVERED NODES\n'+json.dumps(ns)+'\nSOURCE BLOCKS\n'+source(bs)
    r=client.call(d+'/extract_completion',MODELS['extract'],p,9500);assert 'parsed' in r and not r.get('provider_refusal'),r.get('error','Extraction completion failed');g=r['parsed'];g['nodes']=ns+g['nodes'];return g
   assert 'parsed' in r and not r.get('provider_refusal'),r.get('error','Extraction failed');return r['parsed']
  g=stage('dag',extract);graph_check(g,d,bs)
  def generate():
   if baseline:return load(baseline/'paperwise_opus_argon/qa'/f'{d}.json')
   p=prompts['generate']+doc.get('focus','Integrate methods, validation, results and limitations into one scientific decision.')+'\nDAG\n'+json.dumps(g,ensure_ascii=False)+'\nORIGINAL SOURCE BLOCKS\n'+source(bs)
   r=client.call(d+'/generate',MODELS['generate'],p,16000)
   from output_recovery import incomplete_at_limit
   if incomplete_at_limit(r,16000):
    save(dest/'generation_limit_recovery.json',{'reason':'Incomplete output at reported token limit','original_finish_reason':r.get('finish_reason'),'original_completion_tokens':(r.get('usage')or{}).get('completion_tokens'),'original_output_cap':16000,'new_output_cap':32000,'same_scientific_prompt':True,'max_recovery_generations':1})
    r=client.call(d+'/generate_larger_budget',MODELS['generate'],p,32000)
   assert 'parsed' in r and not r.get('provider_refusal'),r.get('error','Generation failed');return r['parsed']
  raw=stage('qa_raw',generate);q=normalize(raw,d,bs,g);qa_check(q,d,bs,g);save(dest/'qa.json',q)
  if raw.get('reasoning_dependencies')!=q.get('reasoning_dependencies'):save(dest/'qa_reference_normalization.json',{'kind':'exact_same_document_namespace_expansion','before':raw.get('reasoning_dependencies'),'after':q.get('reasoning_dependencies'),'scientific_prose_changed':False})
  def verify():
   if baseline:return load(baseline/'paperwise_opus_argon/verification'/f'{d}.json')
   p=prompts['verify']+json.dumps(q,ensure_ascii=False)+'\nORIGINAL SOURCE BLOCKS\n'+source(bs)
   r=client.call(d+'/verify',MODELS['verify'],p,6500);assert 'parsed' in r and not r.get('provider_refusal'),r.get('error','Verification failed');return r['parsed']
  v=stage('argon',verify);review_check(v,bs)
  state={'document_id':d,'status':'argon_pass' if v['verdict']=='pass' and not v['major_issues'] else 'argon_not_pass','origin':'historical_baseline' if baseline else 'live','argon_verdict':v['verdict'],'fingerprint':fp}
 except ContentFiltered as e:state={'document_id':d,'status':'provider_filtered','error':str(e),'origin':'live'}
 except Exception as e:state={'document_id':d,'status':'incomplete','error':str(e),'origin':'historical_baseline' if baseline else 'live'}
 save(dest/'state.json',state);print(d,state['status'],flush=True);return state

def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',default=str(ROOT/'manifest.json'));p.add_argument('--output',required=True);p.add_argument('--import-baseline',help='Parent outputs folder containing both historical experiments; no API calls');p.add_argument('--key-stdin',action='store_true');p.add_argument('--recover-streaming',action='store_true',help='One Astra streaming migration attempt after three non-stream transport failures');p.add_argument('--recover-transport',action='store_true',help='Allow one persistent extra transport attempt per scientific stage after an outage');p.add_argument('--screen-only',action='store_true',help='Run only the DS selection gate; never call downstream models');p.add_argument('--concurrency',type=int,default=2);a=p.parse_args();assert 1<=a.concurrency<=10
 manifest_path=pathlib.Path(a.manifest).resolve();m=load(manifest_path);out=pathlib.Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True);baseline=pathlib.Path(a.import_baseline).resolve() if a.import_baseline else None
 old=load(out/'run_config.json') if (out/'run_config.json').exists() else None
 config={'models':MODELS,'manifest':m,'manifest_base':str(manifest_path.parent),'baseline':str(baseline) if baseline else None,'concurrency':a.concurrency}
 screening_enabled=not baseline and m.get('screening',{}).get('enabled',old.get('screening_enabled',False) if old else True)
 if old is None or 'screening_enabled' in old:config['screening_enabled']=screening_enabled
 assert not a.screen_only or screening_enabled,'Screen-only requires screening enabled; use a fresh output directory for legacy runs'
 if old:assert old==config,'Run configuration changed; use a new output folder'
 save(out/'run_config.json',config);key='' if baseline else (sys.stdin.readline().strip() if a.key_stdin else (os.environ.get('YUNJINTAO_API_KEY') or os.environ.get('YJT_API_KEY','')));assert baseline or key,'Set YUNJINTAO_API_KEY or use --key-stdin'
 client=None if baseline else Client(out,key,m['api_base'],a.concurrency,2,m.get('runtime',{}).get('deadline_seconds',480));prompts=load(ROOT/'pipeline/prompts.json');start=time.monotonic()
 if client is not None:client.transport_recovery=a.recover_transport;client.stream_recovery=a.recover_streaming
 if screening_enabled and not a.screen_only:
  from streaming import execute_streaming
  execute_streaming(m,manifest_path,out,key,client,prompts,a.concurrency,start,execute,selection_gate)
  return
 decisions=None;screen_seconds=0
 if screening_enabled:
  from screening import screen
  screened=screen(manifest_path,out/'selection',Client(out/'selection',key,m['api_base'],4,m.get('screening',{}).get('transport_attempts',2),240),m.get('screening',{}).get('allowed_venues',['Nature']))
  decisions={x['document_id']:x for x in screened['rows']};screen_seconds=time.monotonic()-start
  if a.screen_only:
   save(out/'screening_timing.json',{'wall_seconds':screen_seconds,'downstream_calls':0});return
 science_start=time.monotonic()
 from scheduling import run_overlapping
 from formatting import format_document,finalize
 from lexical_cache import LexicalCache
 import threading
 format_client=None;lexical_cache=None
 if client is not None:
  shared_limit=max(a.concurrency,min(4,max(1,m.get('runtime',{}).get('format_concurrency',4))))
  client.sem=threading.Semaphore(shared_limit)
  format_client=Client(out,key,m['api_base'],shared_limit,2,m.get('runtime',{}).get('deadline_seconds',480))
  # Both clients share the same request limit; nested formatting batches cannot double it.
  format_client.sem=client.sem
  lexical_cache=LexicalCache(ROOT/'pipeline/lexical_seed.json')
 states,formatted,timing=run_overlapping(m['documents'],lambda d:selection_gate(d,out,decisions) or execute(d,out,client,prompts,manifest_path.parent,baseline,m.get('runtime',{})),(lambda d:format_document(out,format_client,d,lexical_cache)) if format_client else None,a.concurrency)
 record={'wall_seconds':timing['scientific_wall_seconds'],'screening_wall_seconds':screen_seconds,'mode':'historical_import' if baseline else 'live_or_resume','states':states}
 with (out/'executions.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
 from report import export
 export(out)
 if client is not None:
  final=finalize(out,format_client,formatted)
  save(out/'end_to_end_timing.json',{'wall_seconds':time.monotonic()-start,'screening_wall_seconds':screen_seconds,**timing,'export_wall_seconds':final['wall_seconds'],'training_records':final['records'],'shared_api_concurrency':shared_limit,'scientific_concurrency':a.concurrency,'timing_note':'Science and formatting overlap; their wall times must not be added.'})
if __name__=='__main__':main()

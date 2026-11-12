import pathlib,sys,json,hashlib,re,collections,statistics
REPO=pathlib.Path(__file__).resolve().parent.parent
R=pathlib.Path(os.environ.get('BIO_QA_WORKSPACE', pathlib.Path.cwd())).resolve()
sys.path[:0]=[str(R/'extras'),str(R/'deps'),str(REPO/'frozen/pipeline'),str(REPO/'pipeline'),str(REPO)]
import run as pipeline
from formatting import validate,identifier_set
from tokenizers import Tokenizer
from jinja2 import Environment
from budget_guard import RATES,get_budget
RATES['claude-sonnet-5-5']=(14.4,57.6,1.152)
load=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();sth=lambda t:hashlib.sha256(t.encode()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def finalize():
 assert all(sha(pathlib.Path(p))==h for p,h in load(R/'protected_external_hashes.json').items()),'Protected source/cache/scientific runtime changed'
 state=load(R/'status.json');docs={d['document_id']:d for d in load(R/'production_manifest.json')};tok=Tokenizer.from_file(str(R/'frozen/tokenizer/tokenizer.json'));env=Environment();env.globals['raise_exception']=lambda msg:(_ for _ in ()).throw(ValueError(msg));template=env.from_string((R/'frozen/tokenizer/chat_template.jinja').read_text());texts=[];messages=[];meta=[];validation=[];calls=[]
 for r in state['results']:
  d=r['document_id'];p=R/'tasks'/d/'papers'/d
  if r['status']!='ready':continue
  doc=docs[d];bs=load(R/doc['sources']);g=load(p/'dag.json');q=load(p/'qa_formatted.json');v=load(R/r['final_review_path']);assert sha(p/'qa_formatted.json')==r['final_qa_sha256'];assert sha(R/r['final_review_path'])==r['final_review_sha256'];assert sha(R/doc['sources'])==r['source_sha256']
  pipeline.graph_check(g,d,bs);pipeline.qa_check(q,d,bs,g);pipeline.review_check(v,bs);assert v['verdict']=='pass' and not v['major_issues'];validate(load(p/'qa.json'),q,identifier_set(bs));assert not re.search(r'\bdocument:',q['question']+q['answer'],re.I)
  from repair_overlay import corrected_version
  effective=corrected_version(r,q,v,bs,g)
  if effective is None:continue
  r,q,v=effective
  m=[{'role':'system','content':'Answer using only the supplied paper. Cite original evidence IDs, distinguish reported results from interpretations and proposals, and acknowledge limitations.'},{'role':'user','content':'SOURCE MATERIAL\n'+pipeline.source(bs)+'\n\nQUESTION\n'+q['question']},{'role':'assistant','content':q['answer']}]
  text=template.render(messages=m,add_generation_prompt=False,enable_thinking=False,preserve_thinking=False,tools=None);ids=tok.encode(text,add_special_tokens=False).ids;decoded=tok.decode(ids,skip_special_tokens=False);normalized=tok.normalizer.normalize_str(text) if tok.normalizer else text;assert decoded==normalized;assert tok.encode(decoded,add_special_tokens=False).ids==ids
  texts.append({'text':text});messages.append({'messages':m});meta.append({'document_id':d,'doi':doc['doi'],'version':doc['version'],'publication_year':doc['publication_year'],'license':doc['license'],'split_family':doc['split_family'],'source_sha256':r['source_sha256'],'qa_sha256':r['final_qa_sha256'],'review_sha256':r['final_review_sha256'],'review_path':r['final_review_path'],'serialized_tokens':len(ids),'answer_tokens':len(tok.encode(q['answer'],add_special_tokens=False).ids),'text_sha256':sth(text),'tokenizer_normalized_text_sha256':sth(decoded),'tokenizer_normalization_changed':decoded!=text,'tokenizer_sha256':sha(R/'frozen/tokenizer/tokenizer.json'),'template_sha256':sha(R/'frozen/tokenizer/chat_template.jinja'),'minor_count':len(v['minor_issues'])});validation.append({'document_id':d,'graph_qa_review_format_export':'pass','normalizer_roundtrip_and_token_ids':'pass','original_text_preserved':True})
 classifications={x['document_id']:x['classification'] for x in load(R/'selection.json')['documents']}
 for row in meta:row['classification']=classifications[row['document_id']]
 for f in R.rglob('attempt_*.json'):
  if 'runs' not in f.relative_to(R).parts:continue
  if f.relative_to(R).parts[:2]==("sample_repair","history"):continue
  if not re.fullmatch(r'attempt_\d+\.json',f.name):continue
  a=load(f);u=a.get('usage') or {};cost=None
  if all(isinstance(u.get(k),(int,float)) for k in ['prompt_tokens','completion_tokens']):
   i,o,h=RATES[a['requested_model']];hit=min(u['prompt_tokens'],max(0,(u.get('prompt_tokens_details') or {}).get('cached_tokens',0)));cost=((u['prompt_tokens']-hit)*i+hit*h+u['completion_tokens']*o)/1e6
  calls.append({'historical_cache': f.relative_to(R).parts[0]=='tasks' and f.relative_to(R).parts[1] in {x['document_id'] for x in load(R/'cached_tasks.json')},'path':str(f.relative_to(R)),'stage':a.get('stage'),'model':a['requested_model'],'cost_cny':cost,'usage':u,'service_seconds':a.get('service_seconds'),'queue_seconds':a.get('queue_seconds'),'error':a.get('error')})
 T=R/'training';T.mkdir(exist_ok=True)
 for n,v in [('final_train',texts),('final_messages',messages),('metadata',meta)]:
  tmp=T/(n+'.jsonl.tmp');tmp.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in v));tmp.replace(T/(n+'.jsonl'))
 assert all(sha(R/p)==h for p,h in load(R/'production_input_hashes.json').items());assert all(sha(R/p)==h for p,h in load(R/'import_hashes.json').items())
 report={'papers':state['total'],'ready':len(texts),'outcomes':dict(collections.Counter(x['status'] for x in state['results'])),'rows':state['results'],'cost_known_cny':sum(x['cost_cny'] or 0 for x in calls if not x['historical_cache']),'cached_historical_cost_cny':sum(x['cost_cny'] or 0 for x in calls if x['historical_cache']),'usage_unknown':sum(x['cost_cny'] is None for x in calls if not x['historical_cache']),'historical_usage_unknown':sum(x['cost_cny'] is None for x in calls if x['historical_cache']),'calls':calls,'independent_quality':state.get('independent_quality',[]),'cost_by_path':{scope:sum(x['cost_cny'] or 0 for x in calls if not x['historical_cache'] and (scope=='independent_review')==x['path'].startswith('independent_review/')) for scope in ['production','independent_review']},'wall_seconds':state['elapsed_seconds'],'cached_frontend':not (R/'acquisition').exists(),'new_download_calls':len(load(R/'acquisition/download_summary.json')) if (R/'acquisition/download_summary.json').exists() else 0,'new_screening_calls':sum('/screen_' in (x['stage'] or '') for x in calls),'cold_end_to_end_measured':False,'budget':state['budget'],'quality_not_expert_certification':True,'export_hashes':{p.name:sha(p) for p in T.glob('*.jsonl')},'export_lengths':meta,'integrity':{'frozen_import_unchanged':True,'prepared_source_unchanged':True,'ready_records_checked':len(validation)},'performance':{m:{'calls':len(cs:=[x for x in calls if x['model']==m]),'mean_service_seconds':statistics.mean(x['service_seconds'] for x in cs if x['service_seconds'] is not None),'cost_known_cny':sum(x['cost_cny'] or 0 for x in cs)} for m in {x['model'] for x in calls}}}
 report['historical_cache_separate']=True;report['protected_external_files_unchanged']=len(load(R/'protected_external_hashes.json'));save(R/'report.json',report);save(R/'validation.json',validation);state['export_status']='verified';save(R/'status.json',state);print(json.dumps({k:v for k,v in report.items() if k not in ['rows','calls','export_lengths','performance']},ensure_ascii=False))
if __name__=='__main__':finalize()

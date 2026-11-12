from pathlib import Path
import json,hashlib,shutil,sys,collections,re
R=Path(__file__).resolve().parent;N=R.parent/'bio-qa1000-20261105';E=R.parent/'bio-e2e'
load=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.parent.mkdir(exist_ok=True,parents=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
assert not (N/'production_manifest.json').exists();s=load(N/'selection.json');assert s['count']==1000
for name in ['frozen','deps','extras']:
 if not (N/name).exists():(N/name).symlink_to((E/name).resolve(),target_is_directory=True)
for name in ['runner.py','finalize.py','preflight.py']:shutil.copy2(E/name,N/name)
shutil.copy2(E/'eval_metadata.jsonl',N/'eval_metadata.jsonl');cfg=load(E/'config.json');cfg.update(budget_cny=3000,workers=128,api_concurrency=128,requests_per_minute=55,seed=20261105);save(N/'config.json',cfg)
sys.path[:0]=[str(N/'extras'),str(N/'deps'),str(N/'frozen/pipeline')]
from lxml import etree as X
import run as pipe
from formatting import validate,identifier_set
old={d['document_id']:d for d in load(E/'production_manifest.json')};manifest=[];protected={};cached=[]
focus=next(iter(old.values()))['focus']
for rec in s['documents']:
 i=rec['document_id'];ep=Path(rec['source_dir']);assert sha(ep/'article.xml')==rec['xml_sha256'];protected[str(ep/'article.xml')]=rec['xml_sha256'];protected[str(ep/'source_validation.json')]=sha(ep/'source_validation.json')
 if rec['existing_e2e_task_dir']:
  od=old[i];assert od['doi']==rec['doi'] and str(od['version'])==str(rec['version']);assert sha(E/'imported'/i/'article.xml')==rec['xml_sha256'];assert sha(E/od['sources'])==od['source_text_sha256']
  doc=dict(od);bs=load(E/od['sources']);shutil.copy2(E/od['sources'],(N/'inputs'/f'{i}.json') if (N/'inputs').exists() else (N.mkdir(exist_ok=True) or N/'unused')) if False else None
  (N/'inputs').mkdir(exist_ok=True);shutil.copy2(E/od['sources'],N/od['sources']);shutil.copytree(E/'tasks'/i,N/'tasks'/i)
  prior=load(E/'tasks'/i/'result.json');rc=load(E/'tasks'/i/'run_config.json');assert rc['models']==cfg['models'] and rc['manifest']['runtime']==cfg['runtime'];assert rc['manifest']['documents'][0]==od
  if prior['status']=='ready':
   p=N/'tasks'/i/'papers'/i;g=load(p/'dag.json');q=load(p/'qa_formatted.json');v=load(N/prior['final_review_path']);pipe.graph_check(g,i,bs);pipe.qa_check(q,i,bs,g);pipe.review_check(v,bs);assert v['verdict']=='pass' and not v['major_issues'];validate(load(p/'qa.json'),q,identifier_set(bs));assert sha(p/'qa_formatted.json')==prior['final_qa_sha256'] and sha(N/prior['final_review_path'])==prior['final_review_sha256']
  cached.append({'document_id':i,'status':prior['status'],'origin':str(E/'tasks'/i),'source_sha256':sha(N/doc['sources'])})
  for f in (E/'tasks'/i).rglob('*'):
   if f.is_file():protected[str(f)]=sha(f)
 else:
  bs=load(ep/'blocks.json');save(N/'inputs'/f'{i}.json',bs);root=X.fromstring((ep/'article.xml').read_bytes(),X.XMLParser(resolve_entities=False,no_network=True));meta=root.find('front/article-meta');years=[int(''.join(e.itertext()).strip()) for e in meta.findall('pub-date/year') if ''.join(e.itertext()).strip().isdigit()];assert years and min(years)>=2020
  tree=root.getroottree()
  for b in bs:
   text=re.sub(r'\s+',' ',''.join(tree.xpath(b['xml_xpath'])[0].itertext())).strip();assert text[b['char_start']:b['char_end']]==b['text']
  doc={'document_id':i,'sources':f'inputs/{i}.json','title':rec['title'],'doi':rec['doi'],'venue':'bioRxiv preprint','split_family':rec['doi'].lower(),'focus':focus,'source_text_sha256':sha(N/'inputs'/f'{i}.json'),'publication_year':min(years),'publication_date':None,'date_provenance':'JATS pub-date/year; exact posting day not verified','version':rec['version'],'license':[e.get('{http://www.w3.org/1999/xlink}href','') for e in meta.findall('permissions/license')],'category':[],'source_scope':'All cached JATS reconstructed body paragraphs/captions/tables/equations; no figure pixels or external supplements','additional_source_ids':[]}
 manifest.append(doc)
for f in (N/'frozen/pipeline').glob('*'):
 if f.is_file():protected[str(f.resolve())]=sha(f)
for n in ['schema.json','node_types.json','edge_types.json','tokenizer/tokenizer.json','tokenizer/chat_template.jinja']:protected[str((N/'frozen'/n).resolve())]=sha(N/'frozen'/n)
save(N/'production_manifest.json',manifest);save(N/'production_input_hashes.json',{str(p.relative_to(N)):sha(p) for p in [N/'production_manifest.json']+list((N/'inputs').glob('*.json'))});save(N/'import_hashes.json',{'selection.json':sha(N/'selection.json'),'eval_metadata.jsonl':sha(N/'eval_metadata.jsonl')});save(N/'protected_external_hashes.json',protected);save(N/'cached_tasks.json',cached)
# Offline finalization records copied historical calls separately from new spending.
f=N/'finalize.py';src=f.read_text();needle="calls.append({'path':str(f.relative_to(R)),";assert needle in src
src=src.replace(needle,"calls.append({'historical_cache': f.relative_to(R).parts[0]=='tasks' and f.relative_to(R).parts[1] in {x['document_id'] for x in load(R/'cached_tasks.json')},'path':str(f.relative_to(R)),")
src=src.replace("'cost_known_cny':sum(x['cost_cny'] or 0 for x in calls)","'cost_known_cny':sum(x['cost_cny'] or 0 for x in calls if not x['historical_cache']),'cached_historical_cost_cny':sum(x['cost_cny'] or 0 for x in calls if x['historical_cache'])")
src=src.replace("'usage_unknown':sum(x['cost_cny'] is None for x in calls)","'usage_unknown':sum(x['cost_cny'] is None for x in calls if not x['historical_cache']),'historical_usage_unknown':sum(x['cost_cny'] is None for x in calls if x['historical_cache'])")
f.write_text(src)
print(json.dumps({'prepared':len(manifest),'cached':cached,'new_documents':1000-len(cached),'protected_files':len(protected)},ensure_ascii=False))

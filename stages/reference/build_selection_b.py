"""Build selection for batch B: same hierarchical stratified method, exclude used 1000 + eval, target N."""
from pathlib import Path
import json,collections,random,hashlib,sys
import os
R=Path(os.environ.get('BIO_PROJECT_ROOT','/data/bio-project'));S=R/'bio-fulltext6676-20261105';N=R/'bio-qa1000b-20261108'
load=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
TARGET=660;SEED=20261108
rs=load(S/'needs51/final_results.json');keep={r['document_id']:r for r in rs if r['decision']=='keep'}
old_sel=load(S/'selection.json');pool=[d for d in old_sel['documents'] if d['document_id'] in keep]
used={x['document_id'] for x in load(R/'bio-qa1000-20261105'/'production_manifest.json')}
evlines=[json.loads(l) for l in (R/'bio-e2e/eval_metadata.jsonl').read_text().splitlines() if l.strip()];evtext=json.dumps(evlines)
pool=[d for d in pool if d['document_id'] not in used and d['document_id'] not in evtext and d['doi'] not in evtext]
print('pool available',len(pool))
key=lambda d,n:d['classification'][n+'_id']
def group(ds,n):
 g=collections.defaultdict(list)
 for d in ds:g[key(d,n)].append(d)
 return g
def topics(ds):return len({key(d,'topic') for d in ds})
def allocate(groups,total,mins):
 ns={k:len(v) for k,v in groups.items()};a=dict(mins);assert sum(a.values())<=total<=sum(ns.values())
 targets={k:total*n/sum(ns.values()) for k,n in ns.items()}
 while sum(a.values())<total:
  k=min((k for k in ns if a[k]<ns[k]),key=lambda k:(-(targets[k]-a[k]),k));a[k]+=1
 return a
g=group(pool,'domain');dq=allocate(g,TARGET,{k:topics(v) for k,v in g.items()});selected=[];quotas=[];rng=random.Random(SEED)
for di,ds in sorted(g.items()):
 sg=group(ds,'subdomain');sq=allocate(sg,dq[di],{k:topics(v) for k,v in sg.items()})
 for si,ss in sorted(sg.items()):
  tg=group(ss,'topic');tq=allocate(tg,sq[si],{k:1 for k in tg})
  for ti,ts in sorted(tg.items()):
   ts=sorted(ts,key=lambda d:d['document_id']);picked=rng.sample(ts,tq[ti]);selected+=picked;c=ts[0]['classification']
   quotas.append({k:c[k] for k in ['domain_id','domain','subdomain_id','subdomain','topic_id','topic']}|{'pool':len(ts),'selected':tq[ti]})
assert len(selected)==TARGET and len({d['document_id'] for d in selected})==TARGET
records=[]
for d in selected:
 i=d['document_id'];e=S/'evidence'/i;v=load(e/'source_validation.json');assert sha(e/'article.xml')==v['xml_sha256']
 records.append({'document_id':i,'doi':d['doi'],'version':d['version'],'title':d['title'],'classification':d['classification'],'source_dir':str(e),'xml_sha256':sha(e/'article.xml'),'screen_result':keep[i],'existing_e2e_task_dir':None})
N.mkdir(exist_ok=True)
(N/'selection.json').write_text(json.dumps({'seed':SEED,'count':TARGET,'pool_count':len(pool),'documents':records,'source_results_sha256':sha(S/'needs51/final_results.json'),'eval_sha256':sha(R/'bio-e2e/eval_metadata.jsonl'),'method':'hierarchical nearest-proportion allocation, minimum1 per occupied topic, deterministic random within topic; excludes batch1 1000'},ensure_ascii=False,indent=2)+'\n')
(N/'quotas.json').write_text(json.dumps({'target':TARGET,'domain_quotas':dq,'topics':len(quotas),'excluded_used':len(used)},ensure_ascii=False,indent=1)+'\n')
print('selected',len(selected),'domains',len(dq),'subdomains',len({q['subdomain_id'] for q in quotas}),'topics',len(quotas))
print('domain quotas',json.dumps(dq,ensure_ascii=False)[:600])

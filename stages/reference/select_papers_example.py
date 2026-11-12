from pathlib import Path
import json,collections,random,hashlib,csv,socket,os,shutil
R=Path(__file__).resolve().parent;O=R.parent/'bio-qa1000-20261105';assert socket.gethostname().split('.')[0]=='node032'
assert not (O/'selection.json').exists();O.mkdir(exist_ok=True)
load=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rs=load(R/'needs51/final_results.json');keep={r['document_id']:r for r in rs if r['decision']=='keep'};assert len(keep)==6626
pool=[d for d in load(R/'selection.json')['documents'] if d['document_id'] in keep]
evpath=R.parent/'bio-e2e/eval_metadata.jsonl';ev=[json.loads(l) for l in evpath.read_text().splitlines() if l.strip()];evtext=json.dumps(ev)
for d in pool:assert d['document_id'] not in evtext and d['doi'] not in evtext
key=lambda d,n:d['classification'][n+'_id']
def allocate(groups,total,mins):
 ns={k:len(v) for k,v in groups.items()};a=dict(mins);assert sum(a.values())<=total<=sum(ns.values())
 targets={k:total*n/sum(ns.values()) for k,n in ns.items()}
 while sum(a.values())<total:
  k=min((k for k in ns if a[k]<ns[k]),key=lambda k:(-(targets[k]-a[k]),k));a[k]+=1
 return a
def group(ds,n):
 g=collections.defaultdict(list)
 for d in ds:g[key(d,n)].append(d)
 return g
def topics(ds):return len({key(d,'topic') for d in ds})
g=group(pool,'domain');dq=allocate(g,1000,{k:topics(v) for k,v in g.items()});selected=[];quotas=[];rng=random.Random(20261105)
for di,ds in sorted(g.items()):
 sg=group(ds,'subdomain');sq=allocate(sg,dq[di],{k:topics(v) for k,v in sg.items()})
 for si,ss in sorted(sg.items()):
  tg=group(ss,'topic');tq=allocate(tg,sq[si],{k:1 for k in tg})
  for ti,ts in sorted(tg.items()):
   ts=sorted(ts,key=lambda d:d['document_id']);picked=rng.sample(ts,tq[ti]);selected+=picked;c=ts[0]['classification'];quotas.append({k:c[k] for k in ['domain_id','domain','subdomain_id','subdomain','topic_id','topic']}|{'pool':len(ts),'selected':tq[ti]})
assert len(selected)==1000 and len({d['document_id'] for d in selected})==1000
records=[]
for d in selected:
 i=d['document_id'];e=R/'evidence'/i;v=load(e/'source_validation.json');assert sha(e/'article.xml')==v['xml_sha256'];records.append({'document_id':i,'doi':d['doi'],'version':d['version'],'title':d['title'],'classification':d['classification'],'source_dir':str(e),'xml_sha256':sha(e/'article.xml'),'screen_result':keep[i],'existing_e2e_task_dir':str(R.parent/'bio-e2e/tasks'/i) if (R.parent/'bio-e2e/tasks'/i).exists() else None})
save=lambda p,x:p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
save(O/'selection.json',{'seed':20261105,'count':1000,'pool_count':6626,'documents':records,'source_results_sha256':sha(R/'needs51/final_results.json'),'eval_sha256':sha(evpath),'method':'hierarchical nearest-proportion allocation, minimum1 per occupied topic, deterministic random within topic'})
save(O/'quotas.json',quotas)
for name,data in [('quotas.csv',quotas),('manifest.csv',[dict(document_id=d['document_id'],doi=d['doi'],version=d['version'],title=d['title'],domain=d['classification']['domain'],subdomain=d['classification']['subdomain'],topic=d['classification']['topic']) for d in records])]:
 with (O/name).open('w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
summary={'count':1000,'domains':10,'subdomains':20,'topics':len(quotas),'domain_quotas':dq,'eval_overlap':0,'source_hashes_verified':1000,'candidate_existing_qa_task_paths':sum(bool(d['existing_e2e_task_dir']) for d in records),'new_api_calls':0,'budget_status':'awaiting_separate_QA_budget','model_cost_reference_cny':176.7263295616/68*1000,'historical_unknown_usage_reference':1,'server':socket.gethostname(),'loadavg':os.getloadavg(),'free_disk_gib':shutil.disk_usage(O).free/1024**3}
save(O/'prepare_summary.json',summary);print(json.dumps(summary,ensure_ascii=False))

import collections,html,json,pathlib,statistics
from tokenizers import Tokenizer
from jinja2 import Environment
ROOT=pathlib.Path(__file__).resolve().parent.parent
load=lambda p:json.loads(pathlib.Path(p).read_text())
def export(out):
 cfg=load(out/'run_config.json');manifest=cfg['manifest'];base=pathlib.Path(cfg['manifest_base']);baseline=pathlib.Path(cfg['baseline']) if cfg['baseline'] else None
 tok=Tokenizer.from_file(str(ROOT/'tokenizer/tokenizer.json'));count=lambda t:len(tok.encode(t,add_special_tokens=False).ids)
 env=Environment();env.globals['raise_exception']=lambda s:(_ for _ in ()).throw(ValueError(s));template=env.from_string((ROOT/'tokenizer/chat_template.jinja').read_text());rows=[];graphs=[];summaries=[];attempts=[]
 if baseline:
  for folder in ['astra_typed_dag','paperwise_opus_argon']:
   for p in (baseline/folder/'runs_v2').glob('*/*.json'):
    if '.request.' in p.name or 'focused' in p.parent.name:continue
    x=load(p);stage='extract' if folder=='astra_typed_dag' else 'generate' if p.parent.name.startswith('generate_') else 'verify'
    doc=next((d['document_id'] for d in manifest['documents'] if d['document_id'] in p.parent.name),None)
    if doc:attempts.append({'document_id':doc,'stage':stage,'record':str(p),'model':x.get('requested_model'),'seconds':x.get('elapsed_seconds',0),'usage':x.get('usage'),'error':x.get('error')})
 else:
  for p in (out/'runs').glob('*/*/attempt_*.json'):
   if '.request.' in p.name or p.parent.name.startswith('whitespace_'):continue
   x=load(p);stage=p.parent.name;attempts.append({'document_id':p.parent.parent.name,'stage':'extract' if stage.startswith('extract') else stage,'record':str(p),'model':x.get('requested_model'),'seconds':x.get('elapsed_seconds',0),'usage':x.get('usage'),'error':x.get('error')})
 for doc in manifest['documents']:
  d=doc['document_id'];dest=out/'papers'/d;s=load(dest/'state.json');bs=load(base/doc['sources']);calls=[x for x in attempts if x['document_id']==d]
  s['api_elapsed_sum_seconds']=round(sum(x['seconds'] for x in calls),2);s['reported_api_tokens']=sum((x['usage'] or {}).get('total_tokens',0) for x in calls);s['usage_missing_attempts']=sum(not x['usage'] for x in calls)
  if (dest/'dag.json').exists():
   g=load(dest/'dag.json');s['dag_nodes']=len(g['nodes']);s['dag_edges']=len(g['edges']);graphs.append(g)
  if (dest/'qa.json').exists():
   q=load(dest/'qa.json');src='\n\n'.join('['+b['id']+'] '+b.get('section','')+'\n'+b['text'] for b in bs)
   messages=[{'role':'system','content':'Answer using only the supplied paper. Cite original evidence IDs, distinguish reported results from interpretations and proposals, and acknowledge limitations.'},{'role':'user','content':'SOURCE MATERIAL\n'+src+'\n\nQUESTION\n'+q['question']},{'role':'assistant','content':q['answer']}]
   serialized=template.render(messages=messages,add_generation_prompt=False,enable_thinking=False,preserve_thinking=False,tools=None)
   s['tokens']={'source':count(src),'question':count(q['question']),'answer':count(q['answer']),'serialized_total':count(serialized)}
   v=load(dest/'argon.json') if (dest/'argon.json').exists() else {};s['ratings']=v.get('ratings');s['minor_issues']=len(v.get('minor_issues',[]));s['major_issues']=len(v.get('major_issues',[]));s['reasoning_assessment']=v.get('reasoning_assessment')
   rows.append({'id':d,'messages':messages,'metadata':{'status':s['status'],'origin':s['origin'],'tokens':s['tokens'],'models':cfg['models'],'split_family':doc.get('split_family',d),'review_scope':'Single Argon review only; no extra model review or automatic revision.','tokenizer':load(ROOT/'tokenizer_manifest.json')}})
  summaries.append(s)
 bymodel={}
 for x in attempts:
  m=bymodel.setdefault(x['model'],{'attempts':0,'seconds':0,'prompt_tokens':0,'completion_tokens':0,'reported_total_tokens':0,'usage_missing_attempts':0});m['attempts']+=1;m['seconds']+=x['seconds'];u=x['usage'] or {};m['usage_missing_attempts']+=not bool(u)
  for k in ['prompt_tokens','completion_tokens']:m[k]+=u.get(k,0)
  m['reported_total_tokens']+=u.get('total_tokens',0)
 costs=None;prices=manifest.get('prices_per_million')
 if prices and all(name in prices for name in bymodel):
  costs={name:(m['prompt_tokens']*prices[name]['input']+m['completion_tokens']*prices[name]['output'])/1e6 for name,m in bymodel.items()}
 totals=[s['api_elapsed_sum_seconds'] for s in summaries];summary={'papers':summaries,'argon_pass_count':sum(s['status']=='argon_pass' for s in summaries),'paper_count':len(summaries),'api_attempts':len(attempts),'by_model':bymodel,'reported_api_tokens':sum(m['reported_total_tokens'] for m in bymodel.values()),'usage_missing_attempts':sum(m['usage_missing_attempts'] for m in bymodel.values()),'speed':{'sum_call_seconds':sum(totals),'mean_paper_call_seconds':statistics.mean(totals) if totals else None,'paper_call_seconds_range':[min(totals),max(totals)] if totals else [],'historical_end_to_end_wall_seconds':None,'note':'Historical sum of recorded call durations, including extraction completion and transport failures; not measured batch wall time. Excludes additional focused/Astra reviews. New executions.jsonl records live/resume wall time separately.'},'cost':{'estimate_by_model':costs,'currency':manifest.get('price_currency'),'invoice_verified':False,'note':'Unknown provider prices => monetary cost unknown. Supplied rates yield a simple input/output estimate only, before cache pricing; missing usage may undercount.'},'quality':{'assessment':'Single Argon verdict and subjective ratings. Four related papers are not an accuracy benchmark.','known_baseline_caveats':'Historical extra inspection reported overstrong invariance and causal conclusions in these candidates. Not re-run and not used as an extra gate here.' if baseline else None,'training_benefit':'not measured','long_context_necessity':'not established'}}
 timing_file=out.parent/'live_timing.json'
 if timing_file.exists():
  summary['live_timing']=load(timing_file)
  acquisition=out.parent/'acquisition_timing.json'
  if acquisition.exists():summary['acquisition']=load(acquisition)
  if summary['live_timing'].get('wall_seconds') is not None:summary['tracked_acquisition_plus_pipeline_seconds']=summary['live_timing']['wall_seconds']+summary.get('acquisition',{}).get('preparation_wall_seconds',0)
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));(out/'usage_ledger.json').write_text(json.dumps(attempts,ensure_ascii=False,indent=2))
 for name,data in [('dags',graphs),('all_candidates',rows),('argon_pass',[r for r in rows if r['metadata']['status']=='argon_pass']),('not_pass',[r for r in rows if r['metadata']['status']!='argon_pass']),('failed',[s for s in summaries if s['status']=='incomplete'])]:
  (out/(name+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data))
 esc=html.escape;h='<!doctype html><meta charset="utf-8"><title>Astra → Opus → Argon 自动化</title><style>body{font:16px/1.7 system-ui;max-width:1150px;margin:40px auto;padding:20px;color:#18393d;background:#f4f7f6}article{background:white;padding:22px;margin:20px 0;border-radius:12px}td,th{padding:10px;text-align:left;border-bottom:1px solid #ccd}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#087a72}</style><h1>一篇论文 → 一条生物推理 QA</h1><p>Astra 抽 DAG → Opus 5.5 生成 → Gemini Argon 审核。无额外复核、无自动科学修订。</p><p>本页：'+('已有四篇首稿的历史基线导入；没有重新调用 API。' if baseline else '本轮运行及缓存续跑结果。')+'</p><p><a href="all_candidates.jsonl">全部 QA</a> · <a href="argon_pass.jsonl">Argon 通过候选</a> · <a href="dags.jsonl">DAG JSONL</a> · <a href="summary.json">结构化汇总</a> · <a href="usage_ledger.json">调用用量明细</a></p><article><h2>速度、长度与质量</h2><table><tr><th>论文</th><th>状态</th><th>DAG 节点</th><th>答案 tokens</th><th>完整序列 tokens</th><th>累计调用秒</th><th>API tokens</th></tr>'
 for s in summaries:h+='<tr>'+''.join('<td>'+esc(str(x))+'</td>' for x in [s['document_id'],s['status'],s.get('dag_nodes'),s.get('tokens',{}).get('answer'),s.get('tokens',{}).get('serialized_total'),s['api_elapsed_sum_seconds'],s['reported_api_tokens']])+'</tr>'
 h+='</table><p>累计调用时长包含截断续接与失败重试，不等于并发批次墙钟时间；首次历史试验未记录统一端到端墙钟时间。训练长度用固定 Qwen3.8-27B tokenizer；API 用量使用服务商返回值，两者不可混用。</p></article><article><h2>花销</h2><pre>'+esc(json.dumps(bymodel,ensure_ascii=False,indent=2))+'</pre><p>当前未取得服务商可核实单价及账单，不能给出可靠人民币或美元金额。已知用量 '+str(summary['reported_api_tokens'])+' tokens，另有 '+str(summary['usage_missing_attempts'])+' 次调用未返回 usage。Opus 返回的 completion_tokens 每次为 16000，应作为服务商口径保留，不等同于可见答案长度。</p></article><article><h2>质量解释</h2><p>Argon 通过 '+str(summary['argon_pass_count'])+'/'+str(len(summaries))+'。这是一次模型审核结果，不等于事实准确率 100%。本流程只保留 Argon 原始意见，不追加审核或科学返工。长上下文必要性与 mid-train 收益未测量。</p></article>'
 if summary.get('live_timing'):
  h+='<article><h2>本次实际端到端时间</h2><pre>'+esc(json.dumps({k:summary[k] for k in ['live_timing','acquisition','tracked_acquisition_plus_pipeline_seconds'] if k in summary},ensure_ascii=False,indent=2))+'</pre><p>双篇并发运行；pipeline wall_seconds 是三阶段实际墙钟时间，包含排队、截断续接和传输重试。准备阶段采用出版社 HTML，未调用 MinerU。尚未下载的补充附件、图像像素不在本次证据范围。</p></article>'
 for s in summaries:
  d=s['document_id'];dest=out/'papers'/d;h+='<article><h2>'+esc(d)+'</h2><pre>'+esc(json.dumps(s,ensure_ascii=False,indent=2))+'</pre>'
  for f,label in [('qa.json','QA 全文'),('argon.json','Argon 原始审核')]:
   if (dest/f).exists():h+='<details><summary>'+label+'</summary><pre>'+esc((dest/f).read_text())+'</pre></details>'
  h+='</article>'
 (out/'report.html').write_text(h);print(json.dumps({'argon_pass':summary['argon_pass_count'],'papers':len(summaries),'api_tokens':summary['reported_api_tokens'],'model_metrics':bymodel},ensure_ascii=False))

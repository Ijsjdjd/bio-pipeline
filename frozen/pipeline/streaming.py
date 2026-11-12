"""Emit screening decisions directly into bounded per-paper pipelines."""
import concurrent.futures as futures
import json,pathlib,threading,time
from client import Client
from lexical_cache import LexicalCache
from model_limits import ModelGate

def dispatch(producer,process,concurrency):
 with futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
  jobs=[]
  def submit(doc,decision):jobs.append(pool.submit(process,doc,decision))
  selection=producer(submit)
  results=[job.result() for job in futures.as_completed(jobs)]
 return selection,results

def execute_streaming(manifest,manifest_path,out,key,science_client,prompts,concurrency,start,execute,selection_gate):
 from screening import screen
 from report import export
 from formatting import format_document,finalize
 root=pathlib.Path(__file__).resolve().parent.parent
 runtime=manifest.get('runtime',{});limit=max(concurrency,min(10,max(1,runtime.get('format_concurrency',4))))
 shared=threading.Semaphore(limit);science_client.sem=shared
 screening_client=Client(out/'selection',key,manifest['api_base'],limit,manifest.get('screening',{}).get('transport_attempts',2),240)
 formatting_client=Client(out,key,manifest['api_base'],limit,2,runtime.get('deadline_seconds',480))
 screening_client.sem=formatting_client.sem=shared
 gates={'gpt-6-astra':ModelGate(runtime.get('astra_concurrency',2))}
 for client in [science_client,screening_client,formatting_client]:client.model_gates=gates
 science_client.recover_empty=runtime.get('recover_empty_responses',True)
 cache=LexicalCache(root/'pipeline/lexical_seed.json')
 def process(doc,decision):
  began=time.monotonic()
  state=selection_gate(doc,out,{doc['document_id']:decision}) or execute(doc,out,science_client,prompts,manifest_path.parent,None,runtime)
  review_done=time.monotonic()
  formatted=format_document(out,formatting_client,doc,cache) if state['status']=='argon_pass' else {'document_id':doc['document_id'],'status':'not_argon_pass'}
  return {'document_id':doc['document_id'],'state':state,'formatting':formatted,'started_after_seconds':began-start,'review_done_after_seconds':review_done-start,'finished_after_seconds':time.monotonic()-start}
 selection,results=dispatch(lambda cb:screen(manifest_path,out/'selection',screening_client,manifest.get('screening',{}).get('allowed_venues',['Nature']),on_decision=cb,concurrency=concurrency),process,concurrency)
 by_id={r['document_id']:r for r in results};results=[by_id[d['document_id']] for d in manifest['documents']]
 record={'wall_seconds':time.monotonic()-start,'mode':'streaming_live_or_resume','states':[r['state'] for r in results],'papers':results}
 with (out/'executions.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
 export(out);final=finalize(out,formatting_client,[r['formatting'] for r in results])
 timing={'wall_seconds':time.monotonic()-start,'screening_wall_seconds':selection['wall_seconds'],'shared_api_concurrency':limit,'paper_concurrency':concurrency,'astra_concurrency':gates['gpt-6-astra'].limit,'new_astra_empty_responses':gates['gpt-6-astra'].empty_count,'training_records':final['records'],'export_wall_seconds':final['wall_seconds'],'papers':results,'timing_note':'Screening, science and formatting overlap. Do not sum stage wall times.'}
 (out/'end_to_end_timing.json').write_text(json.dumps(timing,indent=2))

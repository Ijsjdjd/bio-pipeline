"""Overlap per-paper formatting with science, preserving manifest order."""
import concurrent.futures as futures
import time

def run_overlapping(documents, scientific, formatter, concurrency):
 start=time.monotonic();states={};reports={};first_format=None
 with futures.ThreadPoolExecutor(max_workers=2) as formatting_pool:
  pending={}
  with futures.ThreadPoolExecutor(max_workers=concurrency) as science_pool:
   jobs={science_pool.submit(scientific,d):d for d in documents}
   for job in futures.as_completed(jobs):
    doc=jobs[job];d=doc['document_id'];state=job.result();states[d]=state
    if formatter and state['status']=='argon_pass':
     if first_format is None:first_format=time.monotonic()
     pending[formatting_pool.submit(formatter,doc)]=d
    else:reports[d]={'document_id':d,'status':'not_argon_pass'}
  science_seconds=time.monotonic()-start
  for job in futures.as_completed(pending):reports[pending[job]]=job.result()
 end=time.monotonic()
 return ([states[d['document_id']] for d in documents],
         [reports[d['document_id']] for d in documents],
         {'scientific_wall_seconds':science_seconds,'formatting_wall_seconds':end-first_format if first_format is not None else 0,
          'overlapped_wall_seconds':end-start,'formatting_started_after_seconds':first_format-start if first_format is not None else None})

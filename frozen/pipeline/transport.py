"""HTTP transport with explicit complete-response checks for optional SSE."""
import json,sys,urllib.request,urllib.error

def read_sse(lines):
 body={'choices':[]};message={'role':'assistant','content':''};finish=None;tools={};done=False;events=[]
 def consume(data):
  nonlocal finish,done
  if data.strip()=='[DONE]':done=True;return
  event=json.loads(data)
  if event.get('error'):raise ValueError('stream_error: '+json.dumps(event['error']))
  for k in ['id','model','created','usage']:
   if event.get(k) is not None:body[k]=event[k]
  for choice in event.get('choices',[]):
   if choice.get('index',0)!=0:raise ValueError('Unexpected multiple streamed choices')
   delta=choice.get('delta',{})
   if delta.get('content') is not None:
    if not isinstance(delta['content'],str):raise ValueError('Unsupported streamed content type')
    message['content']+=delta['content']
   for part in delta.get('tool_calls',[]):
    index=part['index'];tool=tools.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
    if part.get('id'):tool['id']+=part['id']
    for k in ['name','arguments']:
     if part.get('function',{}).get(k):tool['function'][k]+=part['function'][k]
   if choice.get('finish_reason') is not None:finish=choice['finish_reason']
 try:
  for raw in lines:
   line=raw.decode('utf-8') if isinstance(raw,bytes) else raw
   line=line.rstrip('\r\n')
   if not line:
    if events:consume('\n'.join(events));events=[]
    if done:break
   elif line.startswith('data:'):events.append(line[5:].lstrip())
  if events:consume('\n'.join(events))
  if not done or finish is None:raise ValueError('missing_terminal: incomplete SSE response')
 except Exception as error:
  return {'transport_error':str(error),'body':{**body,'choices':[]},'stream_complete':False}
 if tools:message['tool_calls']=[tools[k] for k in sorted(tools)]
 body['choices']=[{'index':0,'message':message,'finish_reason':finish}]
 return {'body':body,'stream_complete':True}

def main():
 import time,pathlib,datetime,hashlib
 x=json.load(sys.stdin);status=None;beg=time.monotonic();meta={'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'route':x.get('route'),'stream_requested':x['payload'].get('stream',False),'body_bytes_observed':0}
 path=pathlib.Path(x['evidence_path']) if x.get('evidence_path') else None
 if path:path.parent.mkdir(parents=True,exist_ok=True)
 def persist(**kwargs):
  meta.update(kwargs)
  if path:
   tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(meta,ensure_ascii=False,indent=2));tmp.replace(path)
 def headers(response):
  allowed={'date','server','content-type','content-length','transfer-encoding','connection','retry-after','request-id','x-request-id','x-trace-id','traceparent','cf-ray'}
  persist(http_status=response.status,headers_seconds=time.monotonic()-beg,response_headers={k:v for k,v in response.headers.items() if k.lower() in allowed or k.lower().startswith('x-ratelimit')})
 def lines(response):
  for raw in response:
   meta['body_bytes_observed']+=len(raw)
   if 'first_body_seconds' not in meta:meta['first_body_seconds']=time.monotonic()-beg
   if raw.startswith(b'data:'):
    try:
     event=json.loads(raw[5:])
     if event.get('id'):meta['response_id']=event['id']
     if event.get('usage'):meta['usage_seen']=event['usage']
     for ch in event.get('choices',[]):
      if ch.get('finish_reason'):meta['finish_reason_seen']=ch['finish_reason']
    except Exception:pass
   persist(last_body_seconds=time.monotonic()-beg)
   yield raw
 persist()
 import threading
 def heartbeat():
  while True:
   time.sleep(10)
   if path:
    h=path.with_suffix('.heartbeat.json');tmp=h.with_suffix('.tmp');tmp.write_text(json.dumps({'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'request_elapsed_seconds':time.monotonic()-beg,'last_body_seconds':meta.get('last_body_seconds',meta.get('first_body_seconds')),'body_bytes_observed':meta.get('body_bytes_observed'),'no_client_deadline':True}));tmp.replace(h)
 threading.Thread(target=heartbeat,daemon=True).start()
 try:
  req=urllib.request.Request(x['url'],data=json.dumps(x['payload']).encode(),headers={'Authorization':'Bearer '+x['key'],'Content-Type':'application/json'})
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if x.get('route')=='direct' else urllib.request.build_opener()
  with opener.open(req,timeout=x.get('timeout',465)) as response:
   status=response.status;headers(response)
   if 'text/event-stream' in response.headers.get('Content-Type','').lower():
    result=read_sse(lines(response));persist(finished_seconds=time.monotonic()-beg,stream_complete=result.get('stream_complete'),transport_error=result.get('transport_error'));print(json.dumps({'http_status':status,**result}));return
   first=response.read(1);persist(first_body_seconds=time.monotonic()-beg,body_bytes_observed=len(first));raw=first+response.read();body=raw.decode();persist(body_bytes_observed=len(raw),body_sha256=hashlib.sha256(raw).hexdigest())
 except urllib.error.HTTPError as error:
  status=error.code;headers(error);body=error.read().decode(errors='replace')
 except Exception as error:
  persist(finished_seconds=time.monotonic()-beg,transport_error=str(error));print(json.dumps({'http_status':status,'transport_error':str(error)}));return
 try:parsed=json.loads(body)
 except Exception:parsed={'invalid_json_response':body[:2000]}
 persist(finished_seconds=time.monotonic()-beg,response_id=parsed.get('id'),usage_seen=parsed.get('usage'))
 print(json.dumps({'http_status':status,'body':parsed}))
if __name__=='__main__':main()

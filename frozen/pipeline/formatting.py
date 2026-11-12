# -*- coding: utf-8 -*-
"""Whitespace-only finalization, after the single Argon review; never scientific revision."""
import argparse,concurrent.futures,datetime,hashlib,html,json,os,pathlib,re,sys,time
from tokenizers import Tokenizer
from jinja2 import Environment
from client import Client
ROOT=pathlib.Path(__file__).resolve().parent.parent
MODEL='deepseek-v4.1-flash-1017'
PROMPT='''Split the provided English text into readable WORD TOKENS to restore missing spaces. Return JSON arrays of tokens, NOT continuous text. Example input {"question":"Whydoes PAX5 bind?","answer":"Themodel usesNR2C2."} => {"question":["Why","does","PAX5","bind?"],"answer":["The","model","uses","NR2C2."]}. Each array element must be exactly one ordinary English word or one intact biomedical identifier, with adjacent punctuation attached where appropriate. Citation IDs like [mouse_brain_atlas:b0162] must remain intact. Do NOT concatenate an entire sentence into one token. Preserve every non-whitespace character exactly, in order; do not change spelling, capitalization, numbers, punctuation or Markdown symbols. Preserve all original words, even if a scientific claim seems wrong. Do not review or rewrite the content. Empty fields must be empty arrays. Output only {question:[tokens...],answer:[tokens...]}. We will join tokens with spaces and restore original paragraph boundaries.
INPUT JSON
'''
def load(p):return json.loads(pathlib.Path(p).read_text())
def save(p,x):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def strip(s):return re.sub(r'\s','',s)
def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def protected(s,identifiers=()):
 refs=re.findall(r'\b\w+:b\d{4}\b',s)
 # Names that include multiple capitals, or mixed-case letters with digits. Ordinary glued lowercase words are not identifiers.
 ids=[w for w in identifiers if w in s]
 nums=re.findall(r'(?<![\w])\d+(?:[.,]\d+)+(?![\w])',s)
 return sorted(set(refs+ids+nums))
def identifier_set(blocks):
 words=set(re.findall(r'\b[A-Za-z0-9][A-Za-z0-9]*(?:[-–][A-Za-z0-9]+)*\b',' '.join(b['text'] for b in blocks)))
 return sorted(w for w in words if sum(c.isupper() for c in w)>=2 or (any(c.isalpha() for c in w) and any(c.isdigit() for c in w)))
def preserve_existing_whitespace(original,candidate):
 assert strip(original)==strip(candidate),'Non-whitespace character change'
 def gaps(text):
  result={};n=0
  for ch in text:
   if ch.isspace():result[n]=result.get(n,'')+ch
   else:n+=1
  return result
 old=gaps(original);new=gaps(candidate);chars=strip(original);parts=[]
 for i,ch in enumerate(chars):parts.extend([old.get(i,new.get(i,'')),ch])
 parts.append(old.get(len(chars),new.get(len(chars),'')))
 return ''.join(parts)
def validate(old,new,identifiers=()):
 assert isinstance(new,dict),'Formatter must return an object'
 log={}
 for field in ['question','answer']:
  a=old[field];b=new.get(field);assert isinstance(b,str),'Missing '+field
  assert strip(a)==strip(b),'Non-whitespace character change in '+field
  for token in protected(a,identifiers):
   # Literal conservation also prevents accidental spaces inside citations or named entities.
   assert b.count(token)>=a.count(token),'Split protected token '+token
  changes=[]
  i=j=0
  while j<len(b):
   if i<len(a) and a[i]==b[j]:i+=1;j+=1;continue
   begin=j
   while j<len(b) and b[j].isspace() and (i==len(a) or a[i]!=b[j]):j+=1
   assert j>begin,'Only whitespace insertions are allowed in '+field
   changes.append({'old_start':i,'old_end':i,'old':'','new':b[begin:j]})
  assert i==len(a),'Original whitespace removed in '+field
  log[field]={'before_sha256':sha(a),'after_sha256':sha(b),'non_whitespace_sha256':sha(strip(a)),'non_whitespace_identical':True,'edits':changes,'edit_count':len(changes)}
 return log

def format_document(out,client,doc,lexical_cache=None):
 out=pathlib.Path(out);cfg=load(out/'run_config.json');runtime=cfg['manifest'].get('runtime',{});batch_words=runtime.get('format_batch_words',80);max_output=runtime.get('format_max_tokens',24000)
 d=doc['document_id'];paper=out/'papers'/d;state=load(paper/'state.json')
 if state['status']!='argon_pass':return {'document_id':d,'status':'not_argon_pass'}
 q=load(paper/'qa.json');blocks=load(pathlib.Path(cfg['manifest_base'])/doc['sources']);identifiers=identifier_set(blocks);fields={k:q[k] for k in ['question','answer']};fingerprint=sha(PROMPT+json.dumps(fields,ensure_ascii=False)+MODEL+'lexical_v7_render3'+json.dumps({'batch_words':batch_words,'max_output':max_output},sort_keys=True));cache=paper/'qa_formatted.json';record=paper/'formatting_validation.json'
 if cache.exists() and record.exists() and load(record).get('fingerprint')==fingerprint:
  fixed=load(cache);log=validate(q,fixed,identifiers);return {'document_id':d,'status':'ready','cached':True,'edits':sum(x['edit_count'] for x in log.values())}
 # Ask only for suspicious lexical units, not repeated full scientific prose.
 token_pattern=r'[A-Za-z0-9]+(?:[-–][A-Za-z0-9]+)*'
 vocabulary={w.lower() for b in blocks for w in re.findall(token_pattern,b['text'])}
 tokens=sorted(set(w for value in fields.values() for w in re.findall(token_pattern,value) if len(w)>=3 and w.lower() not in vocabulary and not w.isdigit()))
 errors=[];plan_path=paper/'formatting_plan.json'
 if plan_path.exists():
  plan=load(plan_path);assert plan['fingerprint']==fingerprint,'Formatting plan input changed; use a new output directory'
  mapping=plan['cache_mapping'];tokens=plan['pending_tokens']
 else:
  # Freeze per-paper cache decisions before requests so resume cannot reshuffle batches.
  mapping=lexical_cache.lookup(tokens,d) if lexical_cache else {};tokens=[w for w in tokens if w not in mapping]
  save(plan_path,{'fingerprint':fingerprint,'cache_mapping':mapping,'pending_tokens':tokens})
 cache_hits=len(mapping)
 def repair_batch(pair):
  bi,words=pair
  instruction='Split glued English words into readable word tokens. Return JSON {"splits": {original_token: [word1, word2, ...]}}. Include every supplied token. Keep valid English words and biomedical identifiers as a one-element array. Examples: beforeany -> ["before","any"], 279of -> ["279","of"], LiNGAMcausal -> ["LiNGAM","causal"], Slc6a1 -> ["Slc6a1"]. Joining each array WITHOUT spaces must exactly equal the original token, preserving case and every character. Do not add/remove letters or hyphens. We insert spaces locally. No commentary. TOKENS: '+json.dumps(words)
  for attempt in range(2):
   r=client.call(f'{d}/whitespace_v8_words_{bi}_'+sha(json.dumps(words)+str(max_output))[:12]+('_retry' if attempt else ''),MODEL,instruction,max_output,extra_body={'thinking':{'type':'disabled'}})
   if r.get('account_blocked') or r.get('network_circuit_open') or r.get('budget_blocked'):raise RuntimeError(r.get('error','Dispatch stopped'))
   try:
    assert 'parsed' in r and not r.get('provider_refusal'),r.get('error','No parsed split map')
    splits=r['parsed']['splits'];assert set(splits)==set(words),'Missing or extra tokens'
    for word,pieces in splits.items():assert isinstance(pieces,list) and pieces and all(isinstance(w,str) and w and not re.search(r'\s',w) for w in pieces) and ''.join(pieces)==word,'Altered token '+word
    return splits
   except Exception as e:
    errors.append(str(e))
    if r.get('finish_reason')=='length':break
    instruction+='\nPrevious response failed validation: '+str(e)+'. Return all original tokens with exact character preservation.'
  if len(words)>20:
   mid=len(words)//2;return {**repair_batch((str(bi)+'L',words[:mid])),**repair_batch((str(bi)+'R',words[mid:]))}
  raise ValueError('Word-splitting batch failed: '+str(bi))
 batches=[tokens[i:i+batch_words] for i in range(0,len(tokens),batch_words)]
 try:
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   for splits in pool.map(repair_batch,enumerate(batches)):mapping.update(splits)
  fixed={field:re.sub(token_pattern,lambda m:re.sub(r'\s*([-–])\s*',r'\1',' '.join(mapping.get(m[0],[m[0]]))),value) for field,value in fields.items()}
  for field in fixed:
   fixed[field]=re.sub(r'(\[[^\]\n]*:b\d{4}[^\]\n]*\])(?=[A-Za-z])',r'\1 ',fixed[field])
   fixed[field]=re.sub(r',(?=[A-Za-z])',', ',fixed[field])
   fixed[field]=re.sub(r'(?<=[A-Za-z0-9])(?=\[[^\]\n]*:b\d{4})',' ',fixed[field])
   fixed[field]=re.sub(r'([)%])(?=[A-Za-z])',r'\1 ',fixed[field])
   fixed[field]=re.sub(r'(\][.!?])(?=[A-Za-z])',r'\1 ',fixed[field])
   fixed[field]=re.sub(r'([a-z][.!?])(?=[A-Z][a-z])',r'\1 ',fixed[field])
   fixed[field]=re.sub(r'(?<=[A-Za-z]),(?=\d)',', ',fixed[field])
   fixed[field]=re.sub(r';(?=[A-Za-z])','; ',fixed[field])
   fixed[field]=re.sub(r'([⁰¹²³⁴⁵⁶⁷⁸⁹])(?=[A-Za-z])',r'\1 ',fixed[field])
   fixed[field]=re.sub(r'(?<=[A-Za-z0-9])\.(?=(?:The|This|These|However|AlphaFold|BioID|GET|NR2C2|First|Second|Third|An|It)\b)', '. ',fixed[field])
   fixed[field]=re.sub(r'(\*\*\d+\.)(?=[A-Z])',r'\1 ',fixed[field])
   parts=re.split(r'(\[[^\]\n]*:b\d{4}[^\]\n]*\])',fixed[field]);fixed[field]=''.join(part if i%2 else re.sub(r'(?<=[a-z]):(?=[A-Za-z])',': ',part) for i,part in enumerate(parts))
   for token in sorted(identifiers,key=len,reverse=True):
    if token in fields[field]:fixed[field]=re.sub(r'\s*'.join(map(re.escape,token)),lambda m:token,fixed[field])
   fixed[field]=preserve_existing_whitespace(fields[field],fixed[field])
  log=validate(q,fixed,identifiers)
 except Exception as e:
  errors.append(str(e));save(paper/'formatting_failure.json',{'errors':errors,'model':MODEL});return {'document_id':d,'status':'formatting_failed','errors':errors}
 if lexical_cache:lexical_cache.observe(d,{w:mapping[w] for w in tokens})
 result={**q,**fixed};save(cache,result);save(record,{'model':MODEL,'fingerprint':fingerprint,'checks':log,'prior_errors':errors,'scientific_content_changed':False,'thinking':'disabled','strategy':'source-aware lexical token splitting; adaptive batches','initial_batch_words':batch_words,'lexical_cache_hits':cache_hits,'word_splits':mapping});return {'document_id':d,'status':'ready','cached':False,'edits':sum(x['edit_count'] for x in log.values())}

def finalize(out,client,preformatted=None):
 out=pathlib.Path(out);cfg=load(out/'run_config.json');dest=out/'training';dest.mkdir(parents=True,exist_ok=True);start=time.monotonic()
 if preformatted is None:
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:reports=list(ex.map(lambda doc:format_document(out,client,doc),cfg['manifest']['documents']))
 else:
  by_id={r['document_id']:r for r in preformatted}
  assert len(by_id)==len(preformatted) and set(by_id)=={d['document_id'] for d in cfg['manifest']['documents']},'Incomplete finalization results'
  reports=[by_id[d['document_id']] for d in cfg['manifest']['documents']]

 tok=Tokenizer.from_file(str(ROOT/'tokenizer/tokenizer.json'));count=lambda t:len(tok.encode(t,add_special_tokens=False).ids);env=Environment();env.globals['raise_exception']=lambda s:(_ for _ in ()).throw(ValueError(s));template=env.from_string((ROOT/'tokenizer/chat_template.jinja').read_text());text_rows=[];message_rows=[];metadata=[]
 for result in reports:
  if result['status']!='ready':continue
  d=result['document_id'];paper=out/'papers'/d;doc=next(x for x in cfg['manifest']['documents'] if x['document_id']==d);blocks=load(pathlib.Path(cfg['manifest_base'])/doc['sources']);old=load(paper/'qa.json');q=load(paper/'qa_formatted.json');validate(old,q,identifier_set(blocks))
  source='\n\n'.join('['+b['id']+'] '+b.get('section','')+'\n'+b['text'] for b in blocks)
  messages=[{'role':'system','content':'Answer using only the supplied paper. Cite original evidence IDs, distinguish reported results from interpretations and proposals, and acknowledge limitations.'},{'role':'user','content':'SOURCE MATERIAL\n'+source+'\n\nQUESTION\n'+q['question']},{'role':'assistant','content':q['answer']}]
  text=template.render(messages=messages,add_generation_prompt=False,enable_thinking=False,preserve_thinking=False,tools=None)
  text_rows.append({'text':text});message_rows.append({'messages':messages});metadata.append({'row_index_zero_based':len(text_rows)-1,'document_id':d,'split_family':doc.get('split_family',d),'argon_verdict':'pass','formatter':MODEL,'source_sha256':sha(source),'question_tokens':count(q['question']),'answer_tokens':count(q['answer']),'serialized_total_tokens':count(text),'original_answer_tokens':count(old['answer']),'non_whitespace_unchanged':True,'text_sha256':sha(text),'tokenizer':load(ROOT/'tokenizer_manifest.json')})
  (dest/(d+'.txt')).write_text('QUESTION\n'+q['question']+'\n\nANSWER\n'+q['answer'])
 for name,rows in [('final_train',text_rows),('final_messages',message_rows),('metadata',metadata)]:
  tmp=dest/(name+'.jsonl.tmp');tmp.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows));tmp.replace(dest/(name+'.jsonl'))
 calls=[]
 for p in (out/'runs').glob('*/whitespace_v*/attempt_*.json'):
  if '.request.' not in p.name and not load(p).get('reused_from'):calls.append(load(p))
 summary={'formatter':MODEL,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'wall_seconds':time.monotonic()-start,'records':len(text_rows),'papers':reports,'lengths':metadata,'formatting_api_calls':len(calls),'formatting_reported_api_tokens':sum((x.get('usage')or{}).get('total_tokens',0) for x in calls),'usage_missing_calls':sum(not x.get('usage') for x in calls),'interrupted_requests_without_response':sum(not p.with_name(p.name.replace('.request','')).exists() for p in (out/'runs').glob('*/whitespace_v*/attempt_*.request.json')),'scientific_review_added':False,'data_format':'final_train.jsonl: one text field per row, official Qwen chat template, unmasked full-token mid-training. final_messages.jsonl is the same examples in messages form; choose one representation only.'}
 save(dest/'summary.json',summary)
 esc=html.escape;page='<!doctype html><meta charset="utf-8"><title>Final training QA</title><style>body{font:16px/1.7 system-ui;max-width:1050px;margin:40px auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}article{background:#f4f8f7;padding:22px;margin:20px 0}</style><h1>Final training QA: whitespace-only repair</h1><p>deepseek-v4.1-flash-1017. All non-whitespace characters and existing whitespace preserved; no scientific revision.</p><p><a href="final_train.jsonl">Training JSONL (text)</a> | <a href="final_messages.jsonl">Alternative messages JSONL</a> | <a href="metadata.jsonl">Metadata</a> | <a href="summary.json">Validation and usage</a></p><p>Select only one training representation. Text already includes the fixed Qwen3.8-27B chat template. No truncation or padding.</p>'
 for m in metadata:page+='<article><h2>'+esc(m['document_id'])+'</h2><p>Answer tokens: '+str(m['original_answer_tokens'])+' before; '+str(m['answer_tokens'])+' after. Full sequence: '+str(m['serialized_total_tokens'])+'</p><pre>'+esc((dest/(m['document_id']+'.txt')).read_text())+'</pre></article>'
 (dest/'report.html').write_text(page)
 report=out/'report.html'
 if report.exists() and 'training/report.html' not in report.read_text():report.write_text(report.read_text().replace('<article>','<article><h2>Final whitespace-repaired training data</h2><a href="training/report.html">QA and final training JSONL</a></article><article>',1))
 print(json.dumps({'training_records':len(text_rows),'formatting':reports,'tokens':[(x['document_id'],x['answer_tokens'],x['serialized_total_tokens']) for x in metadata]},ensure_ascii=False),flush=True);return summary

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--key-stdin',action='store_true');a=p.parse_args();out=pathlib.Path(a.output);cfg=load(out/'run_config.json');key=sys.stdin.readline().strip() if a.key_stdin else (os.environ.get('YUNJINTAO_API_KEY') or os.environ.get('YJT_API_KEY',''));assert key,'Set YUNJINTAO_API_KEY or use --key-stdin';finalize(out,Client(out,key,cfg['manifest']['api_base'],cfg['manifest'].get('runtime',{}).get('format_concurrency',4),2,cfg['manifest'].get('runtime',{}).get('deadline_seconds',480)))
if __name__=='__main__':main()

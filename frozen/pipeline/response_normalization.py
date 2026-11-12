"""Read an unsolicited QA tool envelope as data, never execute its tool."""
import hashlib,json

def tool_qa(record,stage,model):
 if model!='claude-opus-5-5' or not stage.endswith(('/generate','/generate_larger_budget')) or record.get('finish_reason')!='tool_calls':return None,None
 message=record['api_response']['choices'][0]['message'];calls=message.get('tool_calls',[])
 assert len(calls)==1 and calls[0].get('type')=='function','Ambiguous tool envelope'
 raw=calls[0]['function']['arguments'];obj=json.loads(raw)
 assert isinstance(obj,dict) and all(isinstance(obj.get(k),str) and obj[k] for k in ['document_id','question','answer'])
 decoded=[]
 for field in ['reasoning_dependencies','limitations']:
  if isinstance(obj.get(field),str):
   value=json.loads(obj[field]);assert isinstance(value,list),'Nested field is not a JSON array'
   obj[field]=value;decoded.append(field)
 assert isinstance(obj.get('reasoning_dependencies'),list) and obj['reasoning_dependencies']
 return obj,{'kind':'qa_tool_envelope_as_data','tool_executed':False,'tool_name':calls[0]['function'].get('name'),'arguments_sha256':hashlib.sha256(raw.encode()).hexdigest(),'decoded_json_arrays':decoded,'question_and_answer_unchanged':True}

def quoted_answer_json(text):
 """Escape literal inner quotes in one clearly delimited top-level QA answer.
 No text rewriting, field invention or truncated-response repair is permitted.
 """
 import re,json,hashlib
 starts=list(re.finditer(r'(?m)^(?P<indent>[ \t]*)"answer"\s*:\s*"',text))
 if len(starts)!=1:raise ValueError('No uniquely delimited answer field')
 start=starts[0];indent=start.group('indent')
 ends=list(re.finditer(r'"\s*,\s*\n'+re.escape(indent)+r'"(?:evidence_ids|reasoning_dependencies|limitations|document_id|question)"\s*:',text[start.end():]))
 if len(ends)!=1:raise ValueError('Ambiguous answer boundary')
 end=start.end()+ends[0].start();body=text[start.end():end];parts=[];changes=[];slashes=0
 for i,ch in enumerate(body):
  if ch=='"' and slashes%2==0:parts.append('\\');changes.append(start.end()+i)
  parts.append(ch);slashes=slashes+1 if ch=='\\' else 0
 if not 0<len(changes)<=20:raise ValueError('No bounded quote-only recovery')
 repaired=text[:start.end()]+''.join(parts)+text[end:];value=json.loads(repaired)
 if not isinstance(value,dict) or not all(isinstance(value.get(k),str) for k in ['document_id','question','answer']):raise ValueError('Not a QA envelope')
 return value,{'kind':'escape_inner_answer_quotes','inserted_escape_positions':changes,'original_sha256':hashlib.sha256(text.encode()).hexdigest(),'repaired_sha256':hashlib.sha256(repaired.encode()).hexdigest(),'answer_characters_not_rewritten':True}

def quoted_review_json(text):
 """Quote-only recovery of complete, single-line Argon prose fields."""
 import re,json,hashlib
 pattern=re.compile(r'(?m)^(?P<prefix>[ \t]*"(?P<field>suggested_fix|reason|claim|qa_quote)"[ \t]*:[ \t]*")(?P<body>[^\r\n]*)(?P<suffix>"[ \t]*,?[ \t]*)$')
 edits=[]
 def fix(match):
  body=match.group('body')
  if re.search(r'"\s*,\s*"[A-Za-z_][A-Za-z_0-9]*"\s*:',body):return match.group(0)
  parts=[];slashes=0
  for i,ch in enumerate(body):
   if ch=='"' and slashes%2==0:parts.append('\\');edits.append({'field':match.group('field'),'position':match.start('body')+i})
   parts.append(ch);slashes=slashes+1 if ch=='\\' else 0
  return match.group('prefix')+''.join(parts)+match.group('suffix')
 repaired=pattern.sub(fix,text)
 if not 0<len(edits)<=40:raise ValueError('No bounded review quote-only recovery')
 value=json.loads(repaired)
 if not isinstance(value,dict) or value.get('verdict') not in ['pass','revise','fail'] or not all(isinstance(value.get(k),list) for k in ['major_issues','minor_issues']):raise ValueError('Not a review envelope')
 return value,{'kind':'escape_inner_review_quotes','edits':edits,'original_sha256':hashlib.sha256(text.encode()).hexdigest(),'repaired_sha256':hashlib.sha256(repaired.encode()).hexdigest(),'verdict_and_ratings_unchanged':True,'prose_not_rewritten':True}

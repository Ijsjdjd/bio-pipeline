"""Staged integrity checks; original scientific acceptance rule is unchanged."""
import re
from workflow_contract import *
from workflow_contract import reference_check as _reference_check
REF=r'biorxiv_[A-Za-z0-9_]+:b\d+'
def strings(x):
 if isinstance(x,str):yield x
 elif isinstance(x,dict):
  for v in x.values():yield from strings(v)
 elif isinstance(x,list):
  for v in x:yield from strings(v)
def reference_check(qa,sources):
 refs=_reference_check(qa,sources)
 for s in strings(qa):
  for m in re.finditer(r'\[([^\[\]]*)(?=\[|$)',s):
   if 'biorxiv_' in m.group(1):raise ValueError('Unclosed citation bracket')
  for m in re.finditer(r'\((biorxiv_[^()]*)\)',s):
   if not re.fullmatch(REF+r'(?:\s*[,;]\s*'+REF+r')*\s*',m.group(1)):
    raise ValueError('Malformed parenthetical citation list: '+m.group(1)[:250])
  for m in re.finditer(r'\[([^\[\]]*)\]',s):
   body=m.group(1)
   if 'biorxiv_' in body and not re.fullmatch(r'\s*'+REF+r'(?:\s*[,;]\s*'+REF+r')*\s*',body):
    raise ValueError('Malformed citation list: '+body[:250])
 return refs

def evidence_binding(sources):
 # Retain units and operators in source text; this is traceability, not entailment.
 return {b['id']:{'text':b['text'],'sha256':fingerprint(b['text']),'section':b.get('section')} for b in sources}

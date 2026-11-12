"""Exact-token consensus only; disagreements never become reusable."""
import json
import threading

VERSION='deepseek-v4.1-flash-1017/whitespace_v7_words'

class LexicalCache:
 def __init__(self,path=None):
  self.lock=threading.Lock();self.observations={}
  if path and path.exists():
   seed=json.loads(path.read_text())
   assert seed['version']==VERSION,'Lexical cache version mismatch'
   for row in seed['papers']:self.observe(row['document_id'],row['word_splits'])

 def observe(self,paper,mapping):
  with self.lock:
   for word,pieces in mapping.items():
    assert isinstance(pieces,list) and pieces and all(isinstance(p,str) and p and not any(c.isspace() for c in p) for p in pieces) and ''.join(pieces)==word
    self.observations.setdefault(word,{})[paper]=tuple(pieces)

 def lookup(self,words,paper):
  with self.lock:
   result={}
   for word in words:
    values=[v for p,v in self.observations.get(word,{}).items() if p!=paper]
    if len(values)>=2 and len(set(values))==1:result[word]=list(values[0])
   return result

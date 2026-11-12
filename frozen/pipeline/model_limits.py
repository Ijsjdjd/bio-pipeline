"""Per-model backpressure; never occupy global slots while queued here."""
import threading

class ModelGate:
 def __init__(self,limit=2,empty_limit=2):
  assert 1<=limit<=10 and empty_limit>=1
  self.limit=limit;self.empty_limit=empty_limit;self.active=0;self.empty_count=0;self.condition=threading.Condition()
 def acquire(self):
  with self.condition:
   while self.active>=self.limit:self.condition.wait()
   self.active+=1;return True
 def release(self):
  with self.condition:self.active-=1;self.condition.notify_all()
 def record_empty(self):
  with self.condition:self.empty_count+=1;self.condition.notify_all()

def empty_response(record):
 if record.get('finish_reason')=='content_filter':return False
 if record.get('empty_provider_response'):return True
 return record.get('http_status')==200 and record.get('finish_reason')=='stop' and not record.get('raw_text') and 'parsed' not in record and bool(record.get('api_response',{}).get('choices'))

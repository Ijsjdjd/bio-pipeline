"""Persistent estimated-spend reservations. This is not a provider billing cap."""
import sqlite3,os,threading,json,datetime
RATES={'gpt-6-astra':(48,192,3.84),'claude-opus-5-5':(28.8,115.2,1.152),'gemini-4-argon':(8.064,40.32,.4032),'deepseek-v4.1-flash-1017':(1.152,4.608,.023)}
class Budget:
 def __init__(self,path,limit):
  self.path=path;self.limit=None if limit is None or str(limit).lower() in ('none','unlimited') else float(limit)
  with self.db() as c:c.execute('CREATE TABLE IF NOT EXISTS charges(id TEXT PRIMARY KEY,model TEXT,reserved REAL,charged REAL,status TEXT,usage TEXT,created TEXT)')
 def db(self):return sqlite3.connect(self.path,timeout=30)
 def reserve(self,identity,model,prompt,max_tokens):
  rate=RATES[model];estimate=((len(prompt.encode('utf-8'))+400)*rate[0]+max_tokens*rate[1])/1e6
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT id FROM charges WHERE id=?',(identity,)).fetchone()
   if row:return True
   used=c.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM charges').fetchone()[0]
   if self.limit is not None and used+estimate>self.limit:return False
   c.execute('INSERT INTO charges VALUES(?,?,?,?,?,?,?)',(identity,model,estimate,None,'reserved',None,datetime.datetime.now(datetime.timezone.utc).isoformat()));return True
 def settle(self,identity,usage):
  with self.db() as c:
   row=c.execute('SELECT model FROM charges WHERE id=?',(identity,)).fetchone()
   if not row:return
   if not isinstance(usage,dict) or not all(isinstance(usage.get(k),(int,float)) for k in ['prompt_tokens','completion_tokens']):c.execute('UPDATE charges SET status=? WHERE id=?',('usage_unknown',identity));return
   model=row[0];i=usage['prompt_tokens'];o=usage['completion_tokens'];detail=usage.get('prompt_tokens_details') or {};cached=min(i,max(0,detail.get('cached_tokens',detail.get('cache_read_tokens',0))));inp,out,hit=RATES[model]
   # Conservative input rate includes cache-write premium; reporting may show a separate base-price scenario.
   cost=((i-cached)*inp+cached*hit+o*out)/1e6
   c.execute('UPDATE charges SET charged=?,status=?,usage=? WHERE id=?',(cost,'usage_known',json.dumps(usage),identity))
 def summary(self):
  with self.db() as c:
   rows=c.execute('SELECT status,COUNT(*),SUM(reserved),SUM(charged) FROM charges GROUP BY status').fetchall();used=c.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM charges').fetchone()[0]
  return {'estimated_limit_cny':self.limit,'accounted_cny':used,'remaining_estimated_cny':None if self.limit is None else self.limit-used,'groups':[{'status':a,'requests':b,'reserved_cny':d,'known_estimated_cny':e} for a,b,d,e in rows],'invoice_cap':False}
_LOCK=threading.Lock();_BUDGET=None
def get_budget():
 global _BUDGET
 path=os.environ.get('BIO_BATCH_BUDGET_DB')
 if not path:return None
 with _LOCK:
  if _BUDGET is None:_BUDGET=Budget(path,os.environ.get('BIO_BATCH_BUDGET_CNY','1000'))
 return _BUDGET

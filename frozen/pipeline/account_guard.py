"""Account-balance latch shared by clients; retry grants are persisted per stage."""
import json,threading,pathlib,os
_LOCK=threading.Lock();_BLOCKED=False
def insufficient(record):
 text=json.dumps(record,ensure_ascii=False).lower()
 return any(s in text for s in ['余额不足','insufficient balance','insufficient account balance','insufficient_quota','credit balance is too low'])
def blocked():
 return False
def record(value):
 global _BLOCKED
 if insufficient(value):
  return True
 return False
def recovery_limit(folder,normal):
 token=os.environ.get('BIO_ACCOUNT_RECOVERY')
 if not token:return normal
 grant=folder/'account_recovery_grant.json'
 if grant.exists():
  saved=json.loads(grant.read_text());return max(normal,saved['attempt_limit']) if saved['token']==token else normal
 existing=[]
 for p in folder.glob('attempt_*.json'):
  if '.request.' not in p.name:
   try:existing.append((int(p.stem.split('_')[-1]),json.loads(p.read_text())))
   except (ValueError,TypeError):pass
 if existing and insufficient(max(existing,key=lambda x:x[0])[1]):
  limit=max(normal,max(n for n,_ in existing)+1)
  grant.write_text(json.dumps({'token':token,'attempt_limit':limit,'reason':'One explicit post-balance-restoration attempt; prior failures retained.'}));return limit
 return normal

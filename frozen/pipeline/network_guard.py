"""Per-process transport outage circuit. Does not turn failures into successes."""
import threading,re
_LOCK=threading.Lock();_STATE={}
def blocked(base):
 with _LOCK:return _STATE.get(base,0)>=2

def record(base,error=None,http_status=None):
 with _LOCK:
  if error and re.search(r'nodename nor servname|Name or service not known|Temporary failure in name resolution|EOF occurred in violation|NETWORK_UNREACHABLE|Network is unreachable|IncompleteRead|read operation timed out|missing_terminal',str(error),re.I):_STATE[base]=_STATE.get(base,0)+1
  elif http_status is not None and not error:_STATE[base]=0

def reset():
 with _LOCK:_STATE.clear()

def incomplete_at_limit(record,cap):
 if record.get('content_filtered') or record.get('finish_reason')=='content_filter' or record.get('provider_refusal'):return False
 if record.get('finish_reason')=='length':return True
 return 'parsed' not in record and bool(record.get('raw_text')) and (record.get('usage') or {}).get('completion_tokens',0)>=cap

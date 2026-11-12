"""Bounded, auditable screening serialization recovery; never change a verdict."""
import copy,json,re

def normalize_references(value,packet):
 value=copy.deepcopy(value);known={b['id'] for b in packet['selected_blocks']};changes=[]
 for evidence in value.get('evidence',[]):
  short=evidence.get('id')
  if isinstance(short,str) and re.fullmatch(r'b\d{4}',short):
   matches=[k for k in known if k.endswith(':'+short)]
   if len(matches)==1:
    evidence['id']=matches[0];changes.append({'before':short,'after':matches[0]})
 return value,changes

def checked_call(client,stage,model,prompt,budget,packet,validator):
 response=client.call(stage,model,prompt,budget,extra_body={'thinking':{'type':'disabled'}})
 audit={'initial_stage':stage,'recovered':False}
 def check(r):
  if r.get('provider_refusal'):raise ValueError('Provider refusal')
  if 'parsed' not in r:raise ValueError(r.get('error','Missing parsed response'))
  v,changes=normalize_references(r['parsed'],packet)
  validator(v,packet['document_id'],packet)
  return v,changes
 try:
  v,changes=check(response);audit['reference_normalization']=changes;return v,audit
 except (AssertionError,KeyError,TypeError,ValueError) as error:
  # Only a length-truncated response or missing required keys qualifies.
  # Unknown references, an invalid scientific gate, refusals, and exhausted
  # transport retries stay blocked rather than being optimized into a pass.
  if response.get('provider_refusal'):raise
  truncated=response.get('finish_reason')=='length'
  missing=isinstance(error,KeyError) and isinstance(response.get('parsed'),dict)
  if not (truncated or missing):raise
  old=response.get('parsed') if missing else None
  repair_prompt=prompt
  if missing:
   repair_prompt+='\nSERIALIZATION COMPLETION ONLY. The previous object is missing a required field. Return the complete schema. Preserve EVERY existing value exactly, including the decision, reasons and evidence. Add missing fields only, based on supplied evidence; do not reconsider the verdict.\nPREVIOUS OBJECT\n'+json.dumps(old,ensure_ascii=False)
  else:
   repair_prompt+='\nThe previous response exceeded its output limit. Return the complete requested JSON concisely, without a preamble or commentary. Use the same screening criteria; do not assume a desired decision.'
  repaired=client.call(stage+'_schema_completion_v1' if missing else stage+'_length_recovery_v1',model,repair_prompt,24000,extra_body={'thinking':{'type':'disabled'}})
  if old is not None and isinstance(repaired.get('parsed'),dict):
   # Only missing keys may be adopted. Existing scientific values are taken
   # verbatim from the original, even if the model redundantly rewrote them.
   extra={k:v for k,v in repaired['parsed'].items() if k not in old}
   audit['ignored_rewrites']=[k for k in old if k in repaired['parsed'] and old[k]!=repaired['parsed'][k]]
   repaired={**repaired,'parsed':{**old,**extra}}
  v,changes=check(repaired)
  if old is not None:
   normalized_old,_=normalize_references(old,packet)
   assert all(k in v and v[k]==val for k,val in normalized_old.items()),'Schema completion changed an existing value'
  audit.update(recovered=True,kind='missing_field_completion' if missing else 'truncation_recovery',reference_normalization=changes)
  return v,audit

"""Offline release contract; diagnostics are not new scientific rejection criteria."""
import hashlib,json,re

def fingerprint(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def version_contract(qa,dag,sources,prompt):
 return {k:fingerprint(v) for k,v in [('qa',qa),('dag',dag),('sources',sources),('verify_prompt',prompt)]}

def require_review_binding(expected,actual):
 if expected!=actual:raise ValueError('Stale review: QA/DAG/source/prompt version changed')

def original_gate(review):
 return review['verdict']=='pass' and not review['major_issues']

def reference_check(qa,sources):
 known={b['id'] for b in sources};text=json.dumps(qa,ensure_ascii=False)
 refs=set(re.findall(r'biorxiv_[A-Za-z0-9_]+:b\d+',text))
 if refs-known:raise ValueError('Unknown evidence IDs: '+str(sorted(refs-known)))
 return sorted(refs)

def graph_identity(before,after):
 # Correct content without changing node identity, type, or dependency topology.
 a={(n['id'],n['type']) for n in before['nodes']};b={(n['id'],n['type']) for n in after['nodes']}
 if a!=b or before['edges']!=after['edges']:raise ValueError('Unexpected graph identity/topology change')

def diagnostics(qa):
 text='\n'.join([qa['question'],qa['answer']]);out=[]
 rules={'internal_dag_leak':r'\bthe DAG\b|\bDAG (?:also|flags|records|limitation)\b','author_attribution_to_check':r'authors? (?:themselves|explicitly)|discussion itself|paper itself notes',
 'known_word_boundary':r'\bscreen often bacteria\b|\bheld the mat\b|\bNo testis shown\b|\bASNAP-tag\b',
 'numeric_source_audit':r'\b\d+(?:\.\d+)?(?:-fold|%)|\bper species\b|µg/mL'}
 for kind,pattern in rules.items():
  for m in re.finditer(pattern,text,re.I):out.append({'kind':kind,'context':text[max(0,m.start()-70):m.end()+100]})
 return out

def verify_cached_prompt(prompt,record):
 expected=hashlib.sha256(prompt.encode()).hexdigest()
 if record.get('prompt_sha256')!=expected:raise ValueError('Cached review belongs to another prompt/QA/source version')

def audit_packet(qa,dag,sources):
 """Evidence-aligned inspection material, not automated scientific certification."""
 source={b['id']:b['text'] for b in sources};nodes=[]
 for n in dag['nodes']:
  nodes.append({'id':n['id'],'epistemic_status':n.get('epistemic_status'),'statement':n.get('statement'),'quantities':n.get('quantities',[]),'assumptions':n.get('assumptions',[]),'limitations':n.get('limitations',[]),'evidence':{i:source[i] for i in n.get('evidence_ids',[]) if i in source}})
 return {'document_id':qa['document_id'],'diagnostics':diagnostics(qa),'node_evidence':nodes,'qa_dependencies':qa.get('reasoning_dependencies',[]),'limitations':qa.get('limitations',[]),'question_characters':len(qa['question']),'answer_characters':len(qa['answer']),'source_characters':sum(len(s) for s in source.values()),'checks_requiring_source_reading':['stock versus final concentration and all dilution steps','total versus per-species/per-group denominator','matched timepoints for percentages/ratios','author statement versus analytical inference','citation supports the exact adjacent claim','metadata agrees with answer','ambiguous word boundaries and scientific terms'],'automatic_scientific_verdict':None}

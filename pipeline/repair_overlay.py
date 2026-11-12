import os,sys
from pathlib import Path
REPO=Path(__file__).resolve().parent.parent
R=Path(os.environ.get('BIO_QA_WORKSPACE', Path.cwd())).resolve()
sys.path[:0]=[str(REPO/'frozen/pipeline'),str(REPO/'pipeline'),str(REPO)]
import json,hashlib
import run as pipeline
load=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def previous_corrected_version(r,q,v,bs,g):
 p=R/'sample_repair/active_versions.json'
 if not p.exists():return r,q,v
 entry=load(p).get(r['document_id'])
 if not entry:return r,q,v
 assert entry['before_sha256']==r['final_qa_sha256']
 if entry['status']!='ready':return None
 qp=R/entry['qa_path'];vp=R/entry['review_path'];assert sha(qp)==entry['qa_sha256'];assert sha(vp)==entry['review_sha256']
 q=load(qp);v=load(vp);pipeline.qa_check(q,r['document_id'],bs,g);pipeline.review_check(v,bs);assert v['verdict']=='pass' and not v['major_issues']
 r={**r,'original_qa_sha256':r['final_qa_sha256'],'final_qa_sha256':sha(qp),'final_review_sha256':sha(vp),'final_review_path':str(vp.relative_to(R)),'corrected_qa_path':str(qp.relative_to(R))}
 return r,q,v

# Versioned audit20 corrections replace originals; rejected new versions never fall back.
def corrected_version(r,q,v,bs,g):
 prior=previous_corrected_version(r,q,v,bs,g)
 if prior is None:return None
 r,q,v=prior
 p=R/'quality_repair20/active_versions.json'
 if not p.exists():return r,q,v
 e=load(p).get(r['document_id'])
 if e is None:return r,q,v
 assert e['before_sha256']==r['final_qa_sha256']
 if e['status']!='ready':return None
 from workflow_contract import version_contract,require_review_binding,reference_check,graph_identity
 qp=R/e['qa_path'];vp=R/e['review_path'];gp=R/e['dag_path']
 assert sha(qp)==e['qa_sha256'];assert sha(vp)==e['review_sha256'];assert sha(gp)==e['dag_sha256']
 cq=load(qp);cv=load(vp);cg=load(gp);graph_identity(g,cg)
 require_review_binding(e['version_contract'],version_contract(cq,cg,bs,load(R/'frozen/pipeline/prompts.json')['verify']))
 pipeline.graph_check(cg,r['document_id'],bs);pipeline.qa_check(cq,r['document_id'],bs,cg);reference_check(cq,bs);pipeline.review_check(cv,bs);assert cv['verdict']=='pass' and not cv['major_issues']
 r={**r,'original_qa_sha256':r['final_qa_sha256'],'final_qa_sha256':sha(qp),'final_review_sha256':sha(vp),'final_review_path':str(vp.relative_to(R)),'corrected_qa_path':str(qp.relative_to(R)),'corrected_dag_path':str(gp.relative_to(R)),'corrected_dag_sha256':sha(gp)}
 return r,cq,cv

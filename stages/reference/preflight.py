import pathlib,sys,tempfile,json,hashlib
R=pathlib.Path(__file__).resolve().parent;sys.path[:0]=[str(R/'extras'),str(R/'deps'),str(R/'frozen/pipeline')]
import run as p
from lexical_cache import LexicalCache
from tokenizers import Tokenizer
from jinja2 import Environment
assert p.ROOT==R/'frozen',(p.ROOT,R/'frozen')
for name in ['schema.json','node_types.json','edge_types.json','pipeline/prompts.json','pipeline/lexical_seed.json','tokenizer/tokenizer.json','tokenizer/chat_template.jinja']:assert (R/'frozen'/name).is_file(),name
LexicalCache(R/'frozen/pipeline/lexical_seed.json');tok=Tokenizer.from_file(str(R/'frozen/tokenizer/tokenizer.json'));assert tok.decode(tok.encode('Cache test',add_special_tokens=False).ids)=='Cache test';Environment().from_string((R/'frozen/tokenizer/chat_template.jinja').read_text())
f=json.loads((R/'test_fixture.json').read_text());prompts=json.loads((R/'frozen/pipeline/prompts.json').read_text());p.MODELS={'extract':'claude-sonnet-5-5','generate':'claude-sonnet-5-5','verify':'gemini-4-argon'}
class Fake:
 calls=0
 def call(self,stage,*args,**kwargs):
  self.calls+=1;return {'parsed':f[{'extract':'dag','generate':'qa','verify':'review'}[stage.split('/')[-1]]]}
with tempfile.TemporaryDirectory() as t:
 base=pathlib.Path(t);(base/'source.json').write_text(json.dumps(f['sources']));c=Fake();a=p.execute(f['doc'],base/'out',c,prompts,base);assert a['status']=='argon_pass',a;assert c.calls==3
 b=p.execute(f['doc'],base/'out',c,prompts,base);assert b['status']=='argon_pass' and c.calls==3,'Cache replay made new call'
h={str(f.relative_to(R)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (R/'frozen').rglob('*') if f.is_file() and '__pycache__' not in f.parts}
(R/'frozen_runtime_hashes.json').write_text(json.dumps(h,indent=2));print('PASS: complete isolated frozen runtime; real prior QA fixture passes DAG->QA->review; resume adds zero calls; tokenizer template load')

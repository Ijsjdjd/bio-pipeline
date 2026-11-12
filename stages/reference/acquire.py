"""Bounded official requester-pays S3 intake. Credentials use boto3 provider chain only."""
import pathlib,json,sys,os,hashlib,re,zipfile,concurrent.futures,time
R=pathlib.Path(__file__).resolve().parent;sys.path[:0]=[str(R/'extras'),str(R/'deps'),str(R/'frozen/pipeline')]
from lxml import etree as E
from screening import packet,PROMPT,MODEL,validate
from intake import txt
from runner import SafeClient,save,load

def acquire(prefix,max_files,max_bytes,key,workers=64):
 import boto3
 from botocore.config import Config
 assert re.fullmatch(r'Current_Content/[A-Za-z]+_\d{4}/',prefix),'Explicit monthly prefix required'
 assert max_files>0 and max_bytes>0
 base=R/'acquisition';base.mkdir(exist_ok=True);s3=boto3.client('s3',region_name='us-east-1',config=Config(connect_timeout=15,read_timeout=90,retries={'max_attempts':0},max_pool_connections=workers));bucket='biorxiv-src-monthly'
 selection=base/'selection.json'
 if selection.exists():
  frozen=load(selection);assert frozen['prefix']==prefix and frozen['max_files']==max_files and frozen['max_bytes']==max_bytes;objects=frozen['objects']
 else:
  objects=[];size=0
  for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket,Prefix=prefix,RequestPayer='requester'):
   for x in page.get('Contents',[]):
    if not x['Key'].endswith('.meca') or not x['Size']:continue
    if size+x['Size']>max_bytes:continue
    objects.append({'key':x['Key'],'bytes':x['Size'],'etag':x['ETag']});size+=x['Size']
    if len(objects)>=max_files:break
   if len(objects)>=max_files:break
  assert objects,'No bounded objects available; archive may not be published'
  save(selection,{'prefix':prefix,'max_files':max_files,'max_bytes':max_bytes,'objects':objects,'sampling':'lexicographic bounded objects, not representative random corpus'})
 def download(o):
  name=pathlib.Path(o['key']).name;dest=base/name;record=base/(name+'.json')
  if record.exists():
   v=load(record)
   if v.get('success'):assert dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()==v['sha256']
   return v
  result={'key':o['key'],'success':False};start=time.time();pending=base/(name+'.pending')
  if pending.exists():return {**result,'error':'previous download interrupted; no automatic replay'}
  pending.write_text('started')
  try:
   response=s3.get_object(Bucket=bucket,Key=o['key'],RequestPayer='requester');part=base/(name+'.part');h=hashlib.sha256();count=0
   with part.open('wb') as f:
    for b in response['Body'].iter_chunks(262144):f.write(b);h.update(b);count+=len(b)
   response['Body'].close();assert count==o['bytes'],'Size mismatch'
   with zipfile.ZipFile(part) as z:assert z.testzip() is None,'CRC mismatch'
   part.replace(dest);result.update(success=True,path=str(dest),bytes=count,sha256=h.hexdigest())
  except Exception as e:result['error_type']=type(e).__name__ # no credentials or signed URLs in logs
  result['seconds']=time.time()-start;save(record,result);return result
 with concurrent.futures.ThreadPoolExecutor(workers) as pool:downloads=list(pool.map(download,objects))
 save(base/'download_summary.json',downloads);rows=[];outcomes=[]
 def process(a):
  if not a.get('success'):return {'status':'download_hold','key':a['key']},None
  try:
   c=SafeClient(base/'screening',key,load(R/'config.json')['api_base'],1,1,900);c.model_gates={}
   with zipfile.ZipFile(a['path']) as z:
    parser=E.XMLParser(resolve_entities=False,no_network=True);manifest=E.fromstring(z.read('manifest.xml'),parser);item=manifest.xpath('//*[local-name()="item" and @type="article"]')[0];member=item.xpath('./*[local-name()="instance" and @media-type="application/xml"]')[0].get('href');assert '..' not in pathlib.PurePosixPath(member).parts and not member.startswith('/');blob=z.read(member)
   root=E.fromstring(blob,parser);meta=root.find('front/article-meta');doi=txt(meta.find('article-id[@pub-id-type="doi"]'));title=txt(meta.find('title-group/article-title'));ver=re.search(r'v(\d+)$',item.get('id',''));assert ver and doi and title
   ver=ver[1];assert item.get('id','').endswith(doi+'v'+ver),'Manifest/XML DOI version mismatch';xmlver=txt(meta.find('article-version'));assert xmlver in [ver,'1.'+ver];assert re.sub(r'\s+',' ',title)==re.sub(r'\s+',' ',txt(item.find('{*}title')))
   years=[int(txt(y)) for y in meta.findall('pub-date/year') if txt(y).isdigit()];assert years and min(years)>=2020,'Date/year hold'
   urls=[e.get('{http://www.w3.org/1999/xlink}href','') for e in meta.findall('permissions/license')];assert any(re.fullmatch(r'https?://creativecommons.org/licenses/by/[^/]+/?',u) for u in urls),'License hold'
   d='biorxiv_'+re.sub('[^A-Za-z0-9]','_',doi)+'_v'+ver;p=R/'imported'/d;p.mkdir(parents=True,exist_ok=True);x={'document_id':d,'doi':doi,'version':ver,'title':title,'type':root.get('article-type'),'abstract':'\n'.join(txt(e) for e in meta.findall('abstract')),'xml_sha256':hashlib.sha256(blob).hexdigest(),'archive_sha256':a['sha256'],'xml_article_version':xmlver,'xml_member':member,'category':[txt(e) for e in meta.findall('article-categories/subj-group/subject')],'license':urls,'archive_month':prefix}
   (p/'article.xml').write_bytes(blob);save(p/'metadata.json',x);blocks=[{'id':d+':b0001','section':'Abstract','text':x['abstract']}]
   def screen(expanded):
    name='fulltext_screen.json' if expanded else 'abstract_screen.json';dest=p/name
    if dest.exists():return load(dest)
    pk=packet(x,{'journal':'bioRxiv preprint','publication_date':None},blocks,expanded);pk.update(venue_eligible=True,article_type=x['type']);save(p/(name+'.input.json'),pk)
    rec=c.call(d+('/screen_expanded' if expanded else '/screen_abstract'),MODEL,PROMPT+'\nINPUT\n'+json.dumps(pk,ensure_ascii=False),12000 if expanded else 6000)
    assert isinstance(rec.get('parsed'),dict),rec.get('error','No screen response');v=rec['parsed'];validate(v,d,pk);result={'success':True,'decision':v['decision'],'review':v,'stage':'expanded' if expanded else 'abstract'};save(dest,result);return result
   first=screen(False)
   if first['decision']=='exclude':return {'document_id':d,'status':'abstract_excluded'},None
   tree=root.getroottree()
   for node in root.xpath('./body//p[not(ancestor::fig) and not(ancestor::table-wrap)] | .//fig/caption | .//table-wrap/caption'):
    content=txt(node);sections=node.xpath('ancestor::sec/title');section=' / '.join(txt(s) for s in sections) or 'Body/caption'
    for offset in range(0,len(content),1800):blocks.append({'id':d+f':b{len(blocks)+1:04d}','section':section,'text':content[offset:offset+1800],'xml_xpath':tree.getpath(node),'char_start':offset,'char_end':min(offset+1800,len(content)),'xml_sha256':x['xml_sha256']})
   assert sum(len(b['text']) for b in blocks[1:])>=5000,'Insufficient body';save(p/'blocks.json',blocks);save(p/'source_validation.json',{'pass':True,'archive_sha256':a['sha256'],'xml_sha256':x['xml_sha256'],'doi_match':True,'manifest_xml_version_match':True})
   second=screen(True)
   if second['decision']!='keep':return {'document_id':d,'status':'fulltext_'+second['decision']},None
   row={'document_id':d,'doi':doi,'version':ver,'title':title,'import_path':'imported/'+d};save(p/'decision.json',{**row,'decision':'keep','last_stage':'expanded'});return {'document_id':d,'status':'keep'},row
  except Exception as e:return {'key':a['key'],'status':'intake_or_screen_hold','error_type':type(e).__name__},None
 with concurrent.futures.ThreadPoolExecutor(workers) as pool:
  for result,row in pool.map(process,downloads):
   outcomes.append(result)
   if row:rows.append(row)
 save(base/'outcomes.json',outcomes);save(R/'manifest.json',rows)
 save(R/'import_hashes.json',{str(p.relative_to(R)):hashlib.sha256(p.read_bytes()).hexdigest() for p in list((R/'imported').rglob('*'))+[R/'manifest.json',R/'eval_metadata.jsonl'] if p.is_file()});return rows

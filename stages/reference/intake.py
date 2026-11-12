import pathlib,json,hashlib,re,collections,sys
R=pathlib.Path(__file__).resolve().parent;sys.path[:0]=[str(R/'extras'),str(R/'deps')]
from lxml import etree as E
load=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def txt(e):return re.sub(r'\s+',' ',''.join(e.itertext())).strip() if e is not None else ''
def main():
 hashes=load(R/'import_hashes.json');assert all(sha(R/p)==h for p,h in hashes.items())
 manifest=load(R/'manifest.json');ev=[json.loads(l) for l in (R/'eval_metadata.jsonl').read_text().splitlines()];eval_dois={str(x.get('canonical_family') or x.get('split_family') or x.get('doi') or '').lower() for x in ev};eval_ids={x['document_id'] for x in ev};seen={};eligible=[];audit=[]
 for x in sorted(manifest,key=lambda x:int(x['version']),reverse=True):
  d=x['document_id'];p=R/x['import_path'];m=load(p/'metadata.json');v=load(p/'source_validation.json');decision=load(p/'decision.json');r={'document_id':d,'status':'hold'}
  try:
   assert decision['decision']=='keep' and decision['last_stage']=='expanded';assert load(p/'fulltext_screen.json')['success'];assert v['pass'];assert sha(p/'article.xml')==m['xml_sha256']
   root=E.fromstring((p/'article.xml').read_bytes(),E.XMLParser(resolve_entities=False,no_network=True));meta=root.find('front/article-meta');assert txt(meta.find('article-id[@pub-id-type="doi"]'))==x['doi'];assert txt(meta.find('title-group/article-title'))==re.sub(r'\s+',' ',x['title'])
   years=[int(txt(e)) for e in meta.findall('pub-date/year') if txt(e).isdigit()];assert years and min(years)>=2020,'Publication year unknown or before 2020'
   urls=[e.get('{http://www.w3.org/1999/xlink}href','') for e in meta.findall('permissions/license')];assert any(re.fullmatch(r'https?://creativecommons.org/licenses/by/[^/]+/?',u) for u in urls),'Not exact CC-BY'
   assert x['doi'].lower() not in eval_dois and d not in eval_ids,'Evaluation overlap';assert x['doi'].lower() not in seen,'Older duplicate DOI version';seen[x['doi'].lower()]=d
   blocks=load(p/'blocks.json');tree=root.getroottree()
   for b in blocks[1:]:
    nodes=tree.xpath(b['xml_xpath']);assert len(nodes)==1 and txt(nodes[0])[b['char_start']:b['char_end']]==b['text'],'Source reconstruction differs'
   original=len(blocks);added=[]
   # Preserve existing IDs; add missing table cell/equation/list text as new evidence only.
   existing={b['xml_xpath'] for b in blocks if 'xml_xpath' in b}
   for node in root.xpath('./body//table-wrap//table | ./body//disp-formula | ./body//list-item[not(.//p)]'):
    xp=tree.getpath(node)
    if xp in existing:continue
    content='\n'.join(' | '.join(txt(cell) for cell in tr.xpath('./th | ./td')) for tr in node.xpath('.//tr')) if node.tag=='table' else txt(node)
    if not content:continue
    for start in range(0,len(content),1800):
     block={'id':d+f':b{len(blocks)+1:04d}','section':'Additional structured table/equation evidence','render_method':'table_rows_cells_v1' if node.tag=='table' else 'normalized_itertext_v1','text':content[start:start+1800],'xml_xpath':xp,'char_start':start,'char_end':min(start+1800,len(content)),'xml_sha256':m['xml_sha256']};blocks.append(block);added.append(block['id'])
   assert len({b['id'] for b in blocks})==len(blocks)
   dest=R/'inputs'/f'{d}.json'
   if dest.exists():assert load(dest)==blocks,'Changed prepared input'
   else:save(dest,blocks)
   doc={'document_id':d,'sources':str(dest.relative_to(R)),'title':x['title'],'doi':x['doi'],'venue':'bioRxiv preprint','split_family':x['doi'].lower(),'focus':'Integrate the biological question, experimental or analytical design, controls, complementary evidence, results, alternative explanations and limitations into one connected biological reasoning QA. Ground every substantive inference in supplied source evidence; do not equate association with causation.','source_text_sha256':sha(dest),'publication_year':min(years),'publication_date':None,'date_provenance':'JATS pub-date/year; exact posting day not verified','version':x['version'],'license':urls,'category':m.get('category',[]),'source_scope':'All cached JATS body paragraphs/captions plus appended table/equation text; no figure pixel interpretation or external supplement retrieval','additional_source_ids':added}
   eligible.append(doc);r.update(status='eligible',year=min(years),old_blocks=original,new_blocks=len(blocks),added_blocks=len(added))
  except Exception as e:r['reason']=str(e)
  audit.append(r)
 save(R/'production_manifest.json',eligible);save(R/'intake_report.json',{'total':len(manifest),'eligible':len(eligible),'outcomes':dict(collections.Counter(a['status'] for a in audit)),'rows':audit,'year_distribution':dict(collections.Counter(x['publication_year'] for x in eligible)),'all_import_hashes_unchanged':True,'new_screening_calls':0,'new_download_calls':0,'notes':'Cached selected fulltext screen used bounded excerpts; production gets complete parsed text. Exact posting dates unknown; year checked against XML, not inferred from archive.'})
 save(R/'production_input_hashes.json',{str(p.relative_to(R)):sha(p) for p in [R/'production_manifest.json']+list((R/'inputs').glob('*.json'))});print('eligible',len(eligible),'held',len(manifest)-len(eligible))
if __name__=='__main__':main()

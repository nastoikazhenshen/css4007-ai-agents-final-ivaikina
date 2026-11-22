import copy,json,hashlib
from datetime import datetime,timezone
import httpx,pytest
from collectors.common.config import load
from collectors.common.runtime import CollectorState,Fetcher,SourceError,fingerprint,safe_url
from collectors.common.pagination import Pager,Page

@pytest.fixture
def state(tmp_path):
 s=CollectorState(tmp_path);yield s;s.close()
def fetch(state,fn,config=None):return Fetcher(config or load('br_pncp'),state,httpx.Client(transport=httpx.MockTransport(fn)),sleep=lambda n:None,interval=0)
URL='https://pncp.gov.br/test'
@pytest.mark.parametrize('status,code,attempts',[(403,'http_403',1),(401,'auth_required',1),(429,'http_429',3),(503,'http_503',3),(500,'other',3)])
def test_bounded_http_statuses(state,status,code,attempts):
 calls=[]
 f=fetch(state,lambda req:(calls.append(1) or httpx.Response(status)))
 with pytest.raises(SourceError) as e:f.get(URL)
 assert e.value.code==code and len(calls)==attempts

def test_retry_after_large_is_not_shortened(state):
 f=fetch(state,lambda r:httpx.Response(429,headers={'Retry-After':'300'}))
 with pytest.raises(SourceError):f.get(URL)
 assert f.requests==1

def test_timeout_retries(state):
 def timed(r):raise httpx.ReadTimeout('timeout')
 f=fetch(state,timed)
 with pytest.raises(SourceError) as e:f.get(URL)
 assert e.value.code=='timeout' and f.requests==3

@pytest.mark.parametrize('body',[b'',b'<html>bad</html>',b'{broken'])
def test_invalid_json(state,body):
 f=fetch(state,lambda r:httpx.Response(200,content=body))
 with pytest.raises(SourceError) as e:f.json(URL)
 assert e.value.code=='parse_error'

def test_no_disabled_source_request(state):
 cfg=load('br_pncp');cfg['enabled']=False;f=fetch(state,lambda r:pytest.fail('network called'),cfg)
 with pytest.raises(SourceError):f.get(URL)
 assert f.requests==0

@pytest.mark.parametrize('url',['https://evil.test/a','http://pncp.gov.br/a','https://user:password@pncp.gov.br/a'])
def test_no_official_host_escape(state,url):
 f=fetch(state,lambda r:pytest.fail('network called'))
 with pytest.raises(SourceError):f.get(url)

def test_redirect_not_followed_outside_allowlist(state):
 f=fetch(state,lambda r:httpx.Response(302,headers={'location':'https://evil.test/pdf'}))
 with pytest.raises(SourceError):f.get(URL)
 assert f.requests==1

def test_challenge_is_not_parsed(state):
 f=fetch(state,lambda r:httpx.Response(200,text='<html>Human Verification CAPTCHA</html>'))
 with pytest.raises(SourceError) as e:f.get(URL)
 assert e.value.code=='captcha'

def test_checkpoint_redaction(state):
 assert 'secret' not in safe_url(URL+'?api_key=secret&offset=1')

def test_documents_saved_and_sha(state,tmp_path):
 body=b'%PDF-1.4\nfixture'
 f=fetch(state,lambda r:httpx.Response(200,text='User-agent: *\nAllow: /') if r.url.path=='/robots.txt' else httpx.Response(200,content=body,headers={'Content-Type':'application/pdf'}))
 result=f.attachment('br_pncp','one',dict(url=URL+'.pdf',title='Public PDF',kind='other'),tmp_path)
 assert result['sha256']==hashlib.sha256(body).hexdigest() and result['download_status']=='downloaded'
 assert state.db.execute('select count(*) from documents').fetchone()[0]==1

def test_robots_disallows_file_without_fetch(state,tmp_path):
 calls=[]
 f=fetch(state,lambda r:(calls.append(r.url.path) or httpx.Response(200,text='User-agent: *\nDisallow: /')))
 d=f.attachment('br_pncp','one',dict(url=URL+'.pdf'),tmp_path)
 assert d['download_status']=='access_restricted' and calls==['/robots.txt']

def test_html_is_not_stored_as_pdf(state,tmp_path):
 f=fetch(state,lambda r:httpx.Response(404) if r.url.path=='/robots.txt' else httpx.Response(200,content=b'<html>Login</html>',headers={'Content-Type':'text/html'}))
 d=f.attachment('br_pncp','one',dict(url=URL+'.pdf'),tmp_path)
 assert d['download_status']=='failed' and not list(tmp_path.glob('documents/*'))

def test_sqlite_dedup_history_and_integrity(state):
 r=dict(source_tender_id='one',title_original='Line project',fetched_at='2026-01-01')
 assert state.merge_records('s',[r])==dict(new=1,changed=0,unchanged=0)
 r['fetched_at']='2026-01-02';assert state.merge_records('s',[r])['unchanged']==1
 r['title_original']='Revised line project';assert state.merge_records('s',[r])['changed']==1
 assert state.db.execute('select count(*) from collector_records').fetchone()[0]==1
 assert state.db.execute('select count(*) from collector_history').fetchone()[0]==2
 assert state.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def pager_for(state,pages,**kwargs):
 f=fetch(state,lambda r:httpx.Response(200,json=pages[str(r.url)]))
 return Pager(f,'test',URL,lambda o,u:Page(o['rows'],o.get('next'),o.get('total')),lambda r:r['id'],**kwargs)

def test_full_pagination_and_total(state):
 p=pager_for(state,{URL:dict(rows=[dict(id='1')],next=URL+'?p=2',total=2),URL+'?p=2':dict(rows=[dict(id='2')],total=2)}).collect()
 assert p.complete and p.pages==2 and len(p.rows)==2
 bad=pager_for(state,{URL:dict(rows=[None],total=1)}).collect();assert not bad.complete and bad.error.code=='parse_error'

def test_bounded_resume_no_loss(state):
 pages={URL:dict(rows=[dict(id='1')],next=URL+'?p=2',total=2),URL+'?p=2':dict(rows=[dict(id='2')],total=2)}
 p=pager_for(state,pages,max_pages=1).collect();assert not p.complete and len(p.rows)==1
 p=pager_for(state,pages,resume=True).collect();assert p.complete and p.pages==1 and len(p.rows)==2

@pytest.mark.parametrize('case',['repeat','drift','missing','empty_next'])
def test_partial_inconsistent_pagination(state,case):
 second=dict(rows=[dict(id='2')],total=2)
 if case=='repeat':second['rows']=[dict(id='1')]
 if case=='drift':second['total']=3
 if case=='missing':second['rows']=[]
 if case=='empty_next':second=dict(rows=[],next=URL+'?p=3',total=2)
 p=pager_for(state,{URL:dict(rows=[dict(id='1')],next=URL+'?p=2',total=2),URL+'?p=2':second}).collect()
 assert not p.complete and p.error and len(p.rows)==1 if case!='missing' else not p.complete and p.error

def test_retains_documents_on_listing_only_refresh(state):
 old=dict(source_tender_id='one',documents=[dict(url=URL+'.pdf',access='public')],raw_payload=dict(documents=[dict(source_url=URL+'.pdf',sha256='abc',download_status='downloaded')]))
 state.merge_records('br_pncp',[old]);new=dict(source_tender_id='one',documents=[],raw_payload={});state.hydrate_documents('br_pncp',new)
 assert new['documents'] and new['raw_payload']['documents'][0]['sha256']=='abc'
 # Explicit fresh empty attachment list is a removal, not a listing-only refresh.
 removed=dict(source_tender_id='one',documents=[],raw_payload=dict(document_listing_checked_at='2026-11-22'))
 state.hydrate_documents('br_pncp',removed);assert removed['documents']==[] and removed['raw_payload']['documents']==[]

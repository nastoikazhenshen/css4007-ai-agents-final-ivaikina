"""Shared, source-scoped transport/state for country collectors. No central-contract edits."""
from __future__ import annotations
import hashlib, io, json, re, sqlite3, subprocess, tempfile, time, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urljoin, parse_qsl, urlencode, urlunsplit
from urllib.robotparser import RobotFileParser
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from collectors.indonesia.collector_state import State

ROOT=Path(__file__).resolve().parents[2]

def utc(): return datetime.now(timezone.utc).isoformat()
def fingerprint(value):
    def stable(x):
        if isinstance(x,dict):return {k:stable(v) for k,v in x.items() if not k.endswith('_checked_at') and k not in {'downloaded_at','fetched_at','observed_at','last_checked_at','local_path','checked_at','http_status','publishedDate'}}
        if isinstance(x,list):return [stable(v) for v in x]
        return x
    return hashlib.sha256(json.dumps(stable(value),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def safe_url(url):
    u=urlsplit(str(url));q=[(k,'REDACTED' if k.lower() in {'api_key','key','token','access_token'} else v) for k,v in parse_qsl(u.query,keep_blank_values=True)]
    return urlunsplit((u.scheme,u.netloc,u.path,urlencode(q),u.fragment))
def schema(value,name):
    obj=json.loads((ROOT/'contracts/schemas'/name).read_text());Draft202012Validator(obj,format_checker=FormatChecker()).validate(value)

def allowed(url,hosts):
    u=urlsplit(str(url));return u.scheme=='https' and u.hostname in hosts and not u.username and not u.password and (u.port is None or u.port==443)

class SourceError(Exception):
    def __init__(self,message,code='other',retryable=False):super().__init__(message);self.code=code;self.retryable=retryable

class CollectorState(State):
    """Reuse Indonesia's raw, document, source-run and SQLite primitives, independently."""
    def __init__(self,directory):
        super().__init__(Path(directory));self.directory=Path(directory)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS checkpoints(scope TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS collector_records(source TEXT,id TEXT,kind TEXT,signature TEXT,payload TEXT,PRIMARY KEY(source,id,kind));
          CREATE TABLE IF NOT EXISTS collector_history(source TEXT,id TEXT,kind TEXT,observed TEXT,signature TEXT,payload TEXT,PRIMARY KEY(source,id,kind,observed));''')
    def checkpoint(self,key,value=None):
        if value is None:
            r=self.db.execute('SELECT payload FROM checkpoints WHERE scope=?',(key,)).fetchone();return json.loads(r[0]) if r else None
        with self.db:self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))
    def hydrate_documents(self,source,record):
        """Keep previous downloaded evidence on listing-only refresh; never resurrect removed URLs."""
        old=self.db.execute('SELECT payload FROM collector_records WHERE source=? AND id=? AND kind=?',(source,str(record['source_tender_id']),'tender')).fetchone()
        if not old:return record
        old=json.loads(old[0]);raw=record['raw_payload'];prior=old.get('raw_payload') or {}
        if source in {'br_pncp','br_compras'} and not raw.get('document_listing_checked_at'):
            record['documents']=old.get('documents',[])
        urls={d.get('url') for d in record['documents']};current={d['source_url']:d for d in raw.get('documents',[])}
        for doc in prior.get('documents',[]):
            if doc.get('source_url') in urls:current.setdefault(doc['source_url'],doc)
        raw['documents']=list(current.values())
        existing={h.get('email')+str(h.get('source_url')) for h in raw.get('document_contact_candidates',[]) if h.get('email')}
        for hint in prior.get('document_contact_candidates',[]):
            if hint.get('source_url') in urls and hint.get('email')+str(hint.get('source_url')) not in existing:raw.setdefault('document_contact_candidates',[]).append(hint)
        return record
    def merge_records(self,source,records,kind='tender'):
        result=dict(new=0,changed=0,unchanged=0)
        with self.db:
            for r in records:
                key=str(r.get('source_tender_id') or r.get('project_key') or r.get('tender_url') or r.get('official_plan_url'))
                sig=fingerprint(r);prev=self.db.execute('SELECT signature FROM collector_records WHERE source=? AND id=? AND kind=?',(source,key,kind)).fetchone()
                change='new' if not prev else 'unchanged' if prev[0]==sig else 'changed';result[change]+=1
                payload=json.dumps(r,ensure_ascii=False)
                self.db.execute('INSERT OR REPLACE INTO collector_records VALUES (?,?,?,?,?)',(source,key,kind,sig,payload))
                if change!='unchanged':self.db.execute('INSERT INTO collector_history VALUES (?,?,?,?,?,?)',(source,key,kind,utc(),sig,payload))
        return result

class CurlTransport(httpx.BaseTransport):
    """Optional execution-environment proxy transport; default deployment uses httpx."""
    def handle_request(self,request):
        if request.method!='GET':raise SourceError('Read-only transport')
        with tempfile.TemporaryDirectory() as temp:
            body=Path(temp)/'body';head=Path(temp)/'headers'
            # URL via stdin, never expose API credentials in argv or diagnostic output.
            config='url = '+json.dumps(str(request.url))+'\n'
            cmd=['curl','--silent','--show-error','--max-time','45','--max-filesize','100000000','--config','-',
                 '--dump-header',str(head),'--output',str(body),'--write-out','%{http_code}']
            for k,v in request.headers.items():
                if k.lower() not in {'host','connection','accept-encoding'}:cmd.extend(['--header',k+': '+v])
            p=subprocess.run(cmd,input=config,text=True,capture_output=True)
            if p.returncode:
                exc=httpx.ReadTimeout if p.returncode==28 else httpx.TransportError
                raise exc('curl failed (exit '+str(p.returncode)+')',request=request)
            raw=head.read_text(errors='replace') if head.exists() else '';block=raw.strip().split('\n\n')[-1]
            hs=[]
            for line in block.splitlines()[1:]:
                k,sep,v=line.partition(':')
                if sep and k.lower() not in {'set-cookie','content-encoding','transfer-encoding','content-length'}:hs.append((k.strip(),v.strip()))
            return httpx.Response(int(p.stdout[-3:]),headers=hs,content=body.read_bytes() if body.exists() else b'',request=request)

class Fetcher:
    def __init__(self,config,state,client=None,sleep=time.sleep,interval=0.3):
        schema(config,'source_config.schema.json');self.config=config;self.state=state;self.client=client or httpx.Client(timeout=45,follow_redirects=False)
        self.sleep=sleep;self.interval=interval;self.last=0.;self.observations=[];self.robots={};self.requests=0
    def get(self,url,*,policy=False):
        if not self.config['enabled'] or self.config['automation_permission']!='confirmed_permitted':raise SourceError('Source automation is disabled or permission unconfirmed','auth_required')
        if not allowed(url,self.config['approved_hosts']):raise SourceError('URL outside approved HTTPS hosts','auth_required')
        if self.config['source_id']=='us_sam' and 'api_key=REDACTED' in url:
            key=os.environ.get('SAM_API_KEY')
            if not key:raise SourceError('SAM_API_KEY required to resume','auth_required')
            u=urlsplit(url);q=[(k,key if k=='api_key' else v) for k,v in parse_qsl(u.query)];url=urlunsplit((u.scheme,u.netloc,u.path,urlencode(q),u.fragment))
        for hop in range(5):
            for attempt in range(3):
                self.sleep(max(0,self.interval-(time.monotonic()-self.last)));self.last=time.monotonic();self.requests+=1
                try:
                    response=self.client.get(url);status=response.status_code
                except httpx.TimeoutException:
                    self.observations.append(dict(url=safe_url(url),status_code=None,observed_at=utc(),note='timeout'))
                    if attempt==2:raise SourceError('timeout','timeout',True)
                    self.sleep(2**attempt);continue
                except httpx.TransportError:
                    self.observations.append(dict(url=safe_url(url),status_code=None,observed_at=utc(),note='transport error'))
                    if attempt==2:raise SourceError('network transport error','other',True)
                    self.sleep(2**attempt);continue
                self.observations.append(dict(url=safe_url(url),status_code=status,observed_at=utc(),note='attempt '+str(attempt+1)))
                self.state.raw(self.config['source_id'],safe_url(url),datetime.now(timezone.utc),response.content)
                if status in {401,403}:raise SourceError('HTTP '+str(status),'http_403' if status==403 else 'auth_required')
                if status in {408,429,500,502,503,504}:
                    ra=response.headers.get('retry-after','');delay=float(ra) if ra.isdigit() else 2**attempt
                    if ra and not ra.isdigit():
                        try:
                            from email.utils import parsedate_to_datetime
                            delay=max(0,(parsedate_to_datetime(ra)-datetime.now(timezone.utc)).total_seconds())
                        except (ValueError,TypeError,OverflowError):pass
                    code='http_429' if status==429 else 'http_503' if status==503 else 'other'
                    if attempt==2 or delay>60:raise SourceError('HTTP '+str(status)+' retry limit / Retry-After',code,True)
                    self.sleep(delay);continue
                if response.is_redirect:
                    target=urljoin(str(response.url),response.headers.get('location',''))
                    if not allowed(target,self.config['approved_hosts']):raise SourceError('Redirect outside approved hosts','auth_required')
                    url=target;break
                if status>=400:raise SourceError('HTTP '+str(status),'other')
                prefix=response.content[:2000].decode(errors='ignore')
                if re.search(r'captcha|Human Verification|URL.*blocked|URL engellendi|Access Denied',prefix,re.I):raise SourceError('Public response is a challenge/block page','captcha')
                return response
            else:raise SourceError('HTTP retry limit')
        raise SourceError('Redirect limit')
    def json(self,url):
        r=self.get(url)
        if r.status_code==204:return None
        try:return r.json()
        except ValueError:raise SourceError('Expected JSON, received invalid/empty response','parse_error')
    def public_file_allowed(self,url):
        if not allowed(url,self.config['approved_hosts']):return False,'unapproved_host'
        u=urlsplit(url);origin=u.scheme+'://'+u.netloc
        if origin not in self.robots:
            try:
                r=self.get(origin+'/robots.txt',policy=True);text=r.text
                if not re.search(r'^User-agent:',text,re.I|re.M):return False,'robots_policy_unresolved'
                parser=RobotFileParser();parser.parse(text.splitlines());self.robots[origin]=parser
            except SourceError as e:
                # A real absent robots file is not an authorization wall. Permission still comes from SourceConfig.
                if str(e) in {'HTTP 404','HTTP 410'}:parser=RobotFileParser();parser.parse([]);self.robots[origin]=parser
                else:return False,'robots_policy_unresolved: '+str(e)
        return (True,None) if self.robots[origin].can_fetch('independent research-TenderScout',url) else (False,'robots_disallowed')
    def attachment(self,source,identifier,doc,directory):
        url=doc['url'];out=dict(source_url=url,title=doc.get('title'),document_kind=doc.get('kind'),download_status='not_downloaded',download_reason=None)
        try:
            ok,why=self.public_file_allowed(url)
            if not ok:raise SourceError(why,'auth_required')
            r=self.get(url);body=r.content;mime=r.headers.get('content-type','').lower()
            if not body or 'text/html' in mime or body.lstrip().startswith((b'<html',b'<!DOCTYPE')):raise SourceError('HTML/login/empty attachment','parse_error')
            pdf=body.startswith(b'%PDF')
            if (urlsplit(url).path.lower().endswith('.pdf') or 'pdf' in mime) and not pdf:raise SourceError('PDF magic missing','parse_error')
            digest=hashlib.sha256(body).hexdigest();suffix='.pdf' if pdf else '.zip' if body.startswith(b'PK') else '.bin'
            dest=Path(directory)/'documents'/(digest+suffix);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
            out.update(download_status='downloaded',downloaded_at=utc(),sha256=digest,local_path=str(dest.resolve()),http_status=r.status_code)
        except SourceError as e:out.update(download_status='access_restricted' if e.code in {'http_403','auth_required','captcha'} else 'failed',download_reason=str(e))
        self.state.document(source,identifier,out);return out

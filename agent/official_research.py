"""Read-only, policy-gated official-source enrichment for eight tender countries.

No unrestricted search, subdomains by inference, authentication, CAPTCHA, JS bypass,
third-party aggregation, or promotion to Active. Unknown evidence never deletes a lead.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
# Two previously audited, read-only Indonesian legacy channels without SourceConfig.
# Restricted SPSE and unverified feeds are deliberately absent.
INDONESIA_LEGACY = {
    "pln_batam": ("ID", frozenset({"www.plnbatam.com"}), ("https://www.plnbatam.com/pengumuman-pengadaan/",)),
    "world_bank": ("ID", frozenset({"search.worldbank.org"}), ("https://search.worldbank.org/api/v2/procnotices",)),
}
EMAIL = re.compile(r'(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-])')
CONTACT_ROLE = re.compile(
    r'procurement|purchasing|tender|bid(?:ding)?|clarif(?:ication)?|buyer|contact|'
    r'compras|contrataç|licitaç|esclarecim|impugnaç|contato|consultas?|'
    r'adquisicion|licitaci[oó]n|contrataci[oó]n|ofertas?|'
    r'ihale|teklif|satın\s*alma|başvuru|'
    r'pengadaan|penawaran|pelelangan|informasi|'
    r'จัดซื้อ|จัดจ้าง|ประกวดราคา|สอบถาม|ติดต่อ', re.I)
PHONE = re.compile(r'(?:(?:\+\d{1,3}[\s.\-]?)?\(?\d{2,4}\)?[\s.\-]){2,4}\d{3,5}')
STATUS_TERMINAL = re.compile(r'\bcontract award\b|\bawarded\b|\bcancelled\b|\bcanceled\b|\banulada\b|\badjudicad[oa]\b|\bannullat[oa]\b|\biptal\b|\bdibatalkan\b|ยกเลิก', re.I)
OFFER = re.compile(r'\b(?:bids?|submission|offers?|proposal|closing|deadline|tender|penawaran|pemasukan|'
    r'propostas?|ofertas?|presentaci[oó]n|apertura|teklif|son\s+tarih|'
    r'ยื่นข้อเสนอ|เสนอราคา|ประกวดราคา)', re.I)
REGISTER = re.compile(r'registration|pendaftaran|inscriç|inscripci[oó]n|kayıt|ลงทะเบียน', re.I)
DATE_NUM = re.compile(r'(?<!\d)(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?!\d)|(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)')
CLOCK = re.compile(r'\b(?:[01]?\d|2[0-3])[:.]\d{2}\b|\b(?:[01]?\d|2[0-3])h(?:[0-5]\d)?\b', re.I)
MONTHS = {
    'january':1, 'jan':1,'januari':1,'janeiro':1,'enero':1,'ocak':1,'มกราคม':1,
    'february':2,'feb':2,'februari':2,'fevereiro':2,'febrero':2,'şubat':2,'กุมภาพันธ์':2,
    'march':3,'mar':3,'maret':3,'março':3,'marzo':3,'mart':3,'มีนาคม':3,
    'april':4,'apr':4,'abril':4,'nisan':4,'เมษายน':4,
    'may':5,'mei':5,'mayo':5,'maio':5,'mayıs':5,'พฤษภาคม':5,
    'june':6,'jun':6,'juni':6,'junho':6,'junio':6,'haziran':6,'มิถุนายน':6,
    'july':7,'jul':7,'juli':7,'julho':7,'julio':7,'temmuz':7,'กรกฎาคม':7,
    'august':8,'aug':8,'agustus':8,'agosto':8,'ağustos':8,'สิงหาคม':8,
    'september':9,'sep':9,'setembro':9,'septiembre':9,'eylül':9,'กันยายน':9,
    'october':10,'oct':10,'oktober':10,'outubro':10,'octubre':10,'ekim':10,'ตุลาคม':10,
    'november':11,'nov':11,'novembro':11,'kasım':11,'พฤศจิกายน':11,
    'december':12,'dec':12,'desember':12,'dezembro':12,'diciembre':12,'aralık':12,'ธันวาคม':12,
}
MONTH_PATTERN = re.compile(r'(\d{1,2})\s+(?:de\s+)?('+'|'.join(sorted(map(re.escape,MONTHS),key=len,reverse=True))+r')\s+(?:de\s+)?(\d{4})',re.I)
ZONE_LABELS = {'WIB':'Asia/Jakarta','WITA':'Asia/Makassar','WIT':'Asia/Jayapura',
               'UTC':'UTC','GMT':'UTC','TÜRKIYE SAATI':'Europe/Istanbul',
               'HORÁRIO DE BRASÍLIA':'America/Sao_Paulo','HORA DE LIMA':'America/Lima',
               'เวลาประเทศไทย':'Asia/Bangkok'}


@dataclass(frozen=True)
class SourcePolicy:
    source_id: str
    country_code: str
    approved_hosts: frozenset[str]
    enabled: bool
    automation_permission: str
    official_endpoints: tuple[str,...]

    @classmethod
    def load(cls, source_id: str, root: Path = ROOT):
        path = root/'collectors'/'sources'/f'{source_id}.json'
        if not path.is_file():
            # Legacy ID channels are exact-host and robots-gated; unverified
            # Indonesian SPSE/VMS/eProc feeds remain disabled.
            if source_id in INDONESIA_LEGACY:
                country, hosts, endpoints=INDONESIA_LEGACY[source_id]
                return cls(source_id,country,hosts,True,'confirmed_permitted',endpoints)
            return cls(source_id,'',frozenset(),False,'unverified',())
        c=json.loads(path.read_text(encoding='utf-8'))
        return cls(source_id,c.get('country_code',''),frozenset(c.get('approved_hosts') or []),
                   c.get('enabled') is True,c.get('automation_permission','unverified'),
                   tuple(x['url'] for x in c.get('endpoints',[]) if x.get('url')))

    def permitted(self, url: str):
        try:
            u=urlsplit(url)
            return (self.enabled and self.automation_permission=='confirmed_permitted'
                    and u.scheme=='https' and u.hostname in self.approved_hosts
                    and not u.username and not u.password and u.port in (None,443)
                    and not u.fragment and not u.hostname.endswith('.local'))
        except (ValueError,TypeError,AttributeError):return False


class ResearchBlocked(RuntimeError):pass


class OfficialReader:
    """Bounded GETs only; validates redirects & robots and offers MockTransport for tests."""
    def __init__(self, policy: SourcePolicy, *, client: httpx.Client | None = None,
                 max_requests: int = 4, max_bytes: int = 8_000_000):
        self.policy=policy
        self.client=client or httpx.Client(timeout=12,follow_redirects=False,
                                          headers={'User-Agent':'public_research-TenderScout/0.1 (+read-only official tender research)'})
        self.max_requests=max_requests
        self.max_bytes=max_bytes
        self.requests=0
        self._robots: dict[str,bool|RobotFileParser]={}

    def _robot(self, host: str):
        if host not in self._robots:
            try:
                r=self.client.get('https://'+host+'/robots.txt',follow_redirects=False,timeout=8)
                if r.status_code in (401,403) or 300 <= r.status_code < 400:
                    self._robots[host]=False
                elif r.status_code in (404,410):
                    self._robots[host]=True
                elif r.status_code == 200 and len(r.content)<=65_536:
                    parser=RobotFileParser();parser.parse(r.text.splitlines());self._robots[host]=parser
                else:self._robots[host]=False
            except (httpx.HTTPError, ValueError):self._robots[host]=False
        return self._robots[host]

    def get(self, url: str):
        if not self.policy.permitted(url):raise ResearchBlocked('source_not_permitted')
        if self.requests>=self.max_requests:raise ResearchBlocked('request_budget')
        p=urlsplit(url);policy=self._robot(p.hostname)
        if policy is False or (isinstance(policy,RobotFileParser) and not policy.can_fetch('public_research-TenderScout',url)):
            raise ResearchBlocked('robots_disallow_or_unavailable')
        self.requests+=1
        try:
            with self.client.stream('GET',url,follow_redirects=False,timeout=15) as r:
                if 300 <= r.status_code < 400:raise ResearchBlocked('redirect_not_followed')
                if r.status_code in (401,403,429):raise ResearchBlocked('access_restricted')
                if r.status_code != 200:raise ResearchBlocked('http_'+str(r.status_code))
                mime=r.headers.get('content-type','').split(';')[0].lower()
                if mime not in ('text/html','text/plain','application/json','application/pdf',
                                'image/png','image/jpeg','image/webp','application/octet-stream'):
                    raise ResearchBlocked('unsupported_content_type')
                chunks=[];size=0
                for x in r.iter_bytes():
                    size+=len(x)
                    if size>self.max_bytes:raise ResearchBlocked('response_too_large')
                    chunks.append(x)
                payload=b''.join(chunks)
                return {'url':url,'content':payload,'content_type':mime,
                        'sha256':hashlib.sha256(payload).hexdigest(),
                        'status_code':200}
        except httpx.HTTPError:raise ResearchBlocked('network_unavailable') from None


def visible_text(blob:bytes,content_type:str):
    if content_type=='application/json':
        try:return json.dumps(json.loads(blob),ensure_ascii=False)[:40_000]
        except (ValueError,UnicodeDecodeError):return ''
    if content_type=='text/html':
        from lxml import html
        try:
            doc=html.fromstring(blob)
            for x in doc.xpath('//script|//style|//noscript|//svg'):x.drop_tree()
            return ' '.join(doc.text_content().split())[:40_000]
        except (ValueError,TypeError):return ''
    if content_type=='text/plain':return blob.decode('utf-8',errors='replace')[:40_000]
    return ''


def parse_dates_with_evidence(text:str,url:str,*,max_candidates:int=8):
    """Return *proposed* dates with exact source excerpts; never decide Active."""
    text=(text or '')[:45_000]
    found=[]
    matches=[]
    matches.extend((m.start(),m.end(),m) for m in DATE_NUM.finditer(text))
    matches.extend((m.start(),m.end(),m) for m in MONTH_PATTERN.finditer(text))
    for start,end,m in sorted(matches,key=lambda p:p[0]):
        before=text[max(0,start-120):start]
        after=text[end:end+110]
        context=(before+text[start:end]+after).strip()
        # Do not treat dated publication or registration-only dates as bid deadlines.
        offer=bool(OFFER.search(context))
        registration=bool(REGISTER.search(context))
        if not offer or (registration and not OFFER.search(before[-55:])):
            continue
        try:
            if m.re is DATE_NUM:
                if m.group(1):year,month,day=map(int,m.group(1,2,3))
                else:day,month,year=map(int,m.group(4,5,6))
            else:day=int(m.group(1));month=MONTHS[m.group(2).lower()];year=int(m.group(3))
            if year>=2400:year-=543  # Thai Buddhist calendar
            iso=datetime(year,month,day).date().isoformat()
        except (ValueError,TypeError,KeyError):continue
        post=text[end:end+55]
        clock=CLOCK.search(post)
        c=clock.group(0).replace('h',':').replace('.',':') if clock else None
        if c and c.endswith(':'):c+='00'
        if c and re.fullmatch(r'\d{1,2}:\d{2}',c):c=c.zfill(5)
        region=None
        upper=context.upper()
        for label,tz in ZONE_LABELS.items():
            if label in upper:region=tz;break
        if not region:region='unconfirmed'
        found.append({'deadline_date_proposed':iso,'deadline_time_proposed':c,
                      'timezone_proposed':region,'source_url':url,
                      'locator':f'text chars {max(0,start-120)}-{min(len(text),end+110)}',
                      'original_excerpt':context[:250],
                      'type':'bid_deadline_candidate_requires_confirmation'})
        if len(found)>=max_candidates:break
    return found


def contact_evidence(text:str,url:str,*,notice_specific:bool):
    """Extract contact leads; verified role only with contextual official notice attribution."""
    contacts=[];seen=set()
    for match in EMAIL.finditer(text or ''):
        email=match.group(0)
        if email.lower() in seen:continue
        seen.add(email.lower())
        left=max(0,match.start()-150);right=min(len(text),match.end()+100)
        excerpt=text[left:right]
        local=text[max(0,match.start()-95):min(len(text),match.end()+45)]
        attribution=bool(CONTACT_ROLE.search(local))
        contacts.append({'email':email,'name':None,'phone':None,'evidence_url':url,
                         'original_excerpt':excerpt[:260],
                         'role_evidence':'explicit_tender_context' if notice_specific and attribution else 'unconfirmed',
                         'procurement_role_verified':bool(notice_specific and attribution)})
        if len(contacts)>=15:break
    return contacts


def official_anchors(content:bytes, base_url:str, policy:SourcePolicy):
    """Only linked official clarification/contact pages, never web search/crawl."""
    from lxml import html
    try:doc=html.fromstring(content)
    except (ValueError,TypeError):return []
    found=[]
    labels=re.compile(r'contact|clarif|question|comunicaç|esclarecim|contato|consulta|licitaci[oó]n|'
                      r'ihale|informaci[oó]n|pengadaan|สอบถาม|ติดต่อ',re.I)
    for a in doc.xpath('//a[@href]'):
        label=' '.join(a.text_content().split())+' '+(a.get('title') or '')
        if not labels.search(label):continue
        url=urljoin(base_url,a.get('href'))
        if policy.permitted(url) and url!=base_url and url not in found:found.append(url)
        if len(found)>=2:break
    return found

"""MEA public procurement table and published bid schedule; no login or CAPTCHA bypass."""
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, parse_qs, urlsplit, urljoin
from lxml import html
from collectors.common.pagination import Page
from collectors.common.records import notice, text, evidenced_contacts, EMAIL
from collectors.common.runtime import SourceError, utc

HOME='https://procurement.mea.or.th'
SEARCH=HOME+'/Procurement/Search'
MONTHS={'ม.ค.':1,'ก.พ.':2,'มี.ค.':3,'เม.ย.':4,'พ.ค.':5,'มิ.ย.':6,'ก.ค.':7,'ส.ค.':8,'ก.ย.':9,'ต.ค.':10,'พ.ย.':11,'ธ.ค.':12}
def thai_date(value,clock=None):
    value=str(value).translate(str.maketrans('๐๑๒๓๔๕๖๗๘๙','0123456789'))
    m=re.search(r'(\d{1,2})\s+('+'|'.join(re.escape(k) for k in MONTHS)+r')\s+(\d{2,4})',value)
    if not m:return None
    year=int(m[3]);year=year+2500 if year<100 else year;year=year-543 if year>=2400 else year
    hh,mm=map(int,(clock or '00:00').split(':'))
    return datetime(year,MONTHS[m[2]],int(m[1]),hh,mm,tzinfo=timezone(timedelta(hours=7))).isoformat()

def listing(options,partition):return SEARCH+'?'+urlencode(dict(start=0,length=min(options.get('page_size',100),100),draw=1))
def loader(fetch,url):
    if not getattr(fetch,'_mea_initialized',False):
        fetch.get(HOME+'/');fetch.get(HOME+'/Procurement');fetch._mea_initialized=True
    q={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}
    q.update(PURCHASE_TYPE_ID='',METHOD_ID='',**{'search[value]':'','search[regex]':'false'})
    try:return fetch.get(SEARCH,form=q).json()
    except ValueError:raise SourceError('MEA search did not return public JSON','parse_error')
def page(obj,url):
    if not isinstance(obj,dict) or not isinstance(obj.get('data'),list) or type(obj.get('recordsFiltered'))is not int:raise SourceError('Missing MEA data/total','parse_error')
    q={k:int(v[0]) for k,v in parse_qs(urlsplit(url).query).items()};offset=q['start'];q['start']+=q['length'];q['draw']+=1
    for r in obj['data']:r['_source_key']=r.get('KEY_ID')
    return Page(obj['data'],SEARCH+'?'+urlencode(q) if offset+len(obj['data'])<obj['recordsFiltered'] else None,obj['recordsFiltered'])
def parse(config,row,observed):
    # BID_DT_TEXT is a day without a closing time. It is a hint until the detail schedule is read.
    r=notice(config,id=row['KEY_ID'],title=row.get('SUBJECT'),buyer='Metropolitan Electricity Authority (MEA)',url=HOME+'/Procurement/Detail/'+row['KEY_ID'],raw=row,published=thai_date(row.get('DETAIL_DT_TEXT')),status='Published procurement notice; schedule requires detail',now=observed)
    r['raw_payload']['bid_date_hint']=row.get('BID_DT_TEXT');r['raw_payload']['document_sale_end_hint']=row.get('DETAIL_END_DT_TEXT')
    return r
def needs_enrichment(record):
    day=thai_date(record['raw_payload'].get('bid_date_hint'),'23:59')
    if day and datetime.fromisoformat(day)<datetime.now(timezone.utc):
        record['raw_payload']['bid_day_certainly_past']=day
        return False
    return True
def enrich(fetch,record):
    url=record['tender_url'];r=fetch.get(url);h=html.fromstring(r.text)
    fields={}
    for tr in h.xpath('//table[contains(@class,"detailTable")]//tr'):
        th=tr.xpath('./th');td=tr.xpath('./td')
        if th and td:fields[text(th[0].text_content())]=text(td[0].text_content())
    title=fields.get('ชื่อโครงการที่จัดซื้อจัดจ้าง')
    if not title or title!=record['title_original']:raise SourceError('MEA detail identity/title mismatch','parse_error')
    schedule=fields.get('กำหนดเสนอราคา','');clocks=re.findall(r'\b\d{1,2}:\d{2}\b',schedule)
    deadline=thai_date(schedule,clocks[-1]) if clocks else None
    sale=fields.get('กำหนดระยะเวลาประกาศและซื้อแบบประกวดราคาอิเล็กทรอนิกส์','')
    sale_dates=sale.split(' - ');sale_end=thai_date(sale_dates[-1],'23:59') if sale else None
    plain=' '.join(h.xpath('//table[contains(@class,"detailTable")]')[0].text_content().split())
    names=re.search(r'ผู้ลงประกาศ\s*:\s*(.*?)\s*ตำแหน่ง\s*:',plain)
    email_matches=EMAIL.findall(plain);contacts=[]
    if names or email_matches or fields.get('สอบถามทางโทรศัพท์'):
        contacts=[dict(name=names[1] if names else None,email=email_matches[0] if email_matches else None,phone=fields.get('สอบถามทางโทรศัพท์'),department=fields.get('หน่วยงานที่่จัดซื้อจัดจ้าง'))]
    cs,ev=evidenced_contacts(contacts,url,'MEA specific procurement detail contact fields',utc())
    record['raw_payload']['contacts']=cs;record['raw_payload']['evidence'].extend(ev)
    record['contact_hints']=[dict(name=c.get('name'),email=c.get('email'),phone=c.get('phone'),source_url=url) for c in cs]
    documents={}
    for a in h.xpath('//table[contains(@class,"detailTable")]//a[@href]'):
        link=urljoin(url,a.get('href'))
        if urlsplit(link).path.lower().endswith('.pdf'):documents[link]=dict(title=a.get('download') or text(a.getparent().text_content())[:250],url=link,kind='other',access='unknown')
    record.update(deadline_text=deadline,raw_status='Published bid schedule',documents=list(documents.values()))
    raw=record['raw_payload'];raw['verified_detail']=dict(source_id=record['source_tender_id'],title=title,submission_deadline=deadline,document_sale_end=sale_end,deadline_basis='MEA กำหนดเสนอราคา published closing time',fields=fields)
    raw.update(detail_url=url,detail_checked_at=utc(),deadline_basis='published bid closing time, Asia/Bangkok')
    if not email_matches:raw['missing_fields'].append('Email not available as text; detail renders email as an image. Name/phone retained.')
    if contacts:raw['missing_fields']=[v for v in raw['missing_fields'] if v!='contacts not published in parsed record']
    return record

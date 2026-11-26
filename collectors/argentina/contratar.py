"""Official public works notices. ASP.NET anonymous navigation; no login or challenge bypass."""
import re
from urllib.parse import urljoin
from lxml import html
from collectors.common.records import notice,text
from collectors.common.pagination import Page
from collectors.common.runtime import SourceError,utc
URL='https://contratar.gob.ar/ListarAperturaProxima.aspx'
def listing(options,partition):return URL

def loader(fetch,url):
    if not getattr(fetch,'_ar_initialized',False):fetch.get('https://contratar.gob.ar/');fetch._ar_initialized=True
    return fetch.get(url).text

def page(body,url):
    h=html.fromstring(body);tables=h.xpath('//table[contains(@id,"GridListaPliegos")]')
    if not tables:raise SourceError('CONTRATAR public list not received; redirect/login/shell is not an empty result','parse_error')
    rows=[]
    for tr in tables[0].xpath('.//tr[td]'):
        cells=tr.xpath('./td');a=tr.xpath('.//a[contains(@href,"lnkNumeroProceso")]')
        if len(cells)<6 or not a:continue
        target=re.search(r"__doPostBack\('([^']+)'",a[0].get('href',''))
        if not target:continue
        rows.append(dict(id=text(cells[0].text_content()),title=text(cells[1].text_content()),opening=text(cells[3].text_content()),status=text(cells[4].text_content()),buyer=text(cells[5].text_content()),target=target[1]))
    return Page(rows,None,len(rows))
def parse(config,row,observed):
    # The published list contains an opening date, NOT a submission deadline.
    return notice(config,id=row['id'],title=row['title'],buyer=row['buyer'],url=None,raw=row,status=row['status'],now=observed,locator='CONTRATAR public upcoming openings list')
def enrich(fetch,r):
    body=loader(fetch,URL);h=html.fromstring(body);form={i.get('name'):i.get('value','') for i in h.xpath('//input[@type="hidden"][@name]')};form['__EVENTTARGET']=r['raw_payload']['source_record']['target'];form['__EVENTARGUMENT']=''
    response=fetch.get(URL,form=form);tree=html.fromstring(response.text)
    for e in tree.xpath('//script|//style'):e.drop_tree()
    content=text(tree.text_content())
    if r['source_tender_id'] not in content:raise SourceError('CONTRATAR detail identity not confirmed','parse_error')
    raw=r['raw_payload'];raw['detail_url']=str(response.url);raw['detail_checked_at']=utc();raw['verified_detail']={'public_text':content,'process_number':r['source_tender_id']};r['description_original']=content
    r['tender_url']=str(response.url)
    # Read the exact published deadline label; never substitute the opening time.
    match=re.search(r'Fecha y hora fin recepci[oó]n de documentos en soporte f[ií]sico\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})',content,re.I)
    if match:
        from datetime import datetime
        r['deadline_text']=datetime.strptime(match[1]+' '+match[2],'%d/%m/%Y %H:%M').isoformat()
        raw['verified_detail']['submission_deadline']=r['deadline_text'];raw['verified_detail']['deadline_basis']='physical_documents_submission';raw['verified_detail']['published_state']=r['raw_status']
        raw['deadline_label_original']=match[0]
    # Supplemental official ministry publication, already registered for this programme.
    # Primary discovery remains the full current public CONTRATAR list, not this known example.
    if re.search(r'\bAMBA\s+I\b',r['title_original'],re.I):
        ministry='https://www.argentina.gob.ar/economia/energia/licitacion-amba-i'
        ok,why=fetch.public_file_allowed(ministry)
        if ok:
            mh=html.fromstring(fetch.get(ministry).text);main=mh.xpath('//main') or [mh];docs={}
            for a in main[0].xpath('.//a[@href]'):
                link=urljoin(ministry,a.get('href'))
                if link.startswith('https://www.argentina.gob.ar/') and link.lower().endswith('.pdf'):
                    docs[link]=dict(title=text(a.text_content()) or 'Official ministry circular',url=link,kind='other',access='unknown')
            r['documents']=list(docs.values());raw['supplemental_document_index']=ministry
        else:raw['supplemental_document_error']=why
    return r

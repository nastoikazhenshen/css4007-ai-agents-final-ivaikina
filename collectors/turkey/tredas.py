"""Read-only DataTables request explicitly used by TREDAS public current-tenders page."""
import json
from urllib.parse import urlencode,urljoin
from lxml import html
from collectors.common.pagination import Page
from collectors.common.records import notice,text
from collectors.common.runtime import SourceError
from collectors.common.relevance import assess
URL='https://tredas.com.tr/modules/load/bids'
OPTIONS={'type':'bids','options':{'campus_id':'','title':{'tr':'','en':''},'show_title':'1','category':['3'],'qty':'99999','ordering':'date_desc','view_type':'table','columns':['ihale_no','ihale_title','start_date','end_date','statu','publish_file_url'],'show_archive_btn':'1','show_pagination':'1','css_rule':'','box_width':'boxed','parameters':'','bg_color':'','mt':'0','ml':'0','mr':'0','mb':'0','pt':'0','pl':'0','pr':'0','pb':'0'}}
def listing(options,partition):return URL+'?'+urlencode(dict(start=0,length=100,draw=1))
def must_inspect(row):
    # The public current-tender table often uses generic headings; scope is in its notice.
    return bool(row.get('publish_file_url'))
def loader(fetch,url):
    from urllib.parse import parse_qs,urlsplit
    q={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()};
    def flatten(prefix,value):
        if isinstance(value,dict):
            for k,v in value.items():yield from flatten(prefix+'['+k+']',v)
        elif isinstance(value,list):
            for i,v in enumerate(value):yield from flatten(prefix+'['+str(i)+']',v)
        else:yield prefix,str(value)
    q.update(dict(flatten('mod_data',OPTIONS)))
    if not getattr(fetch,'_tredas_public_initialized',False):
        fetch.get('https://tredas.com.tr/tr/guncel-ihaleler');fetch._tredas_public_initialized=True
    r=fetch.get(URL,form=q)
    try:return r.json()
    except ValueError:raise SourceError('TREDAS listing did not return JSON','parse_error')
def page(obj,url):
    from urllib.parse import parse_qs,urlsplit
    if not isinstance(obj,dict) or not isinstance(obj.get('data'),list):raise SourceError('Invalid TREDAS DataTables envelope','parse_error')
    rows=obj['data'];total=obj.get('recordsFiltered');q={k:int(v[0]) for k,v in parse_qs(urlsplit(url).query).items()};offset=q['start'];q['start']+=q['length'];q['draw']+=1
    if type(total)is not int:raise SourceError('Missing TREDAS total','parse_error')
    for i,r in enumerate(rows):r['_source_key']=str(r.get('id') or r.get('ihale_no') or r.get('publish_file_url'))
    return Page(rows,URL+'?'+urlencode(q) if offset+len(rows)<total else None,total)
def parse(config,row,observed):
    value=row.get('publish_file_url') or '';h=html.fromstring(str(value) or '<span/>');links=h.xpath('//a/@href');url=urljoin(config['official_homepage'],links[0]) if links else None
    record=notice(config,id=row['_source_key'],title=text(row.get('ihale_title')),buyer='Trakya Elektrik Dağıtım A.Ş.',url=url,raw=row,published=None,status=text(row.get('statu')),deadline=text(row.get('end_date')) or None,documents=[dict(title='Official tender notice',url=url,kind='tender_notice',access='unknown')] if url else [],now=observed,locator='TREDAS public current bids table')
    scope=assess(record['title_original'])
    if url and scope['reason'].startswith('No contextual evidence'):
        record['raw_payload']['scope_document_review_required']=True
    return record

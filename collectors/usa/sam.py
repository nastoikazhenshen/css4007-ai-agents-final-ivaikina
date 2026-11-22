"""SAM public opportunities API; a real SAM_API_KEY is required. Never stored in config."""
import os
from urllib.parse import urlencode,urlsplit,parse_qs
from datetime import date
from collectors.common.pagination import Page
from collectors.common.records import notice
from collectors.common.runtime import SourceError
API='https://api.sam.gov/opportunities/v2/search'
def listing(options,partition):
    key=os.environ.get('SAM_API_KEY')
    if not key:raise SourceError('SAM_API_KEY is required; no data request made','auth_required')
    params=dict(api_key=key,postedFrom=date.fromisoformat(options['start']).strftime('%m/%d/%Y'),postedTo=date.fromisoformat(options['end']).strftime('%m/%d/%Y'),limit=options.get('page_size',1000),offset=0)
    return API+'?'+urlencode(params)
def page(obj,url):
    if not isinstance(obj,dict):raise SourceError("Expected JSON object envelope","parse_error")
    rows=obj.get('opportunitiesData');total=obj.get('totalRecords')
    if not isinstance(rows,list) or type(total) is not int:raise SourceError('Invalid SAM envelope','parse_error')
    q={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()};offset=int(q['offset']);limit=int(q['limit']);q['offset']=offset+1
    return Page(rows,API+'?'+urlencode(q) if offset*limit+len(rows)<total else None,total)
def parse(config,row,observed):
    id=row['noticeId'];url=row.get('uiLink') or 'https://sam.gov/opp/'+id+'/view'
    cs=[dict(name=c.get('fullName'),email=c.get('email'),phone=c.get('phone')) for c in row.get('pointOfContact') or []]
    docs=[dict(title='SAM resource link',url=u,kind='other',access='unknown') for u in row.get('resourceLinks') or [] if str(u).startswith('https://')]
    return notice(config,id=id,title=row.get('title'),buyer=row.get('fullParentPathName'),url=url,raw=row,description=row.get('description'),published=row.get('postedDate'),status=str(row.get('active')),deadline=row.get('responseDeadLine') or row.get('reponseDeadLine'),contacts=cs,documents=docs,now=observed,locator='SAM noticeId='+id)

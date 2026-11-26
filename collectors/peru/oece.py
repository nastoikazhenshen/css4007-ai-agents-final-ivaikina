"""Official OECE OCDS records; dates/status remain source facts, not Active claims."""
from urllib.parse import urlencode
from collectors.common.pagination import Page
from collectors.common.runtime import SourceError
from collectors.common.records import notice
API='https://contratacionesabiertas.oece.gob.pe/api/v1/'
def listing(options,partition):
    params=dict(sourceId=options.get('oece_source','seace_v3'),dataSegmentationID=partition)
    if options.get('oece_pagination')=='cursor':params['size']=min(options.get('page_size',100),100)
    else:params['page']=1
    if options.get('query'):params['tenderTitle']=options['query']
    if options.get('category'):params['mainProcurementCategory']=options['category']
    return API+('recordsAfter' if options.get('oece_pagination')=='cursor' else 'records')+'?'+urlencode(params)
def page(obj,url):
    if not isinstance(obj,dict):raise SourceError("Expected JSON object envelope","parse_error")
    rows=obj.get('records');links=obj.get('links')
    if not isinstance(rows,list) or not isinstance(links,dict):raise SourceError('Invalid OCDS record package','parse_error')
    return Page(rows,links.get('next'),None)
def parse(config,row,observed):
    c=row.get('compiledRelease')
    if not isinstance(c,dict) or not row.get('ocid'):raise SourceError('OCDS record has no compiledRelease / ocid','parse_error')
    tender=c.get('tender') or {};buyer=c.get('buyer') or tender.get('procuringEntity') or {};contacts=[]
    for party in c.get('parties') or []:
        if set(party.get('roles',[])) & {'buyer','procuringEntity'}:
            cp=dict(party.get('contactPoint') or {});contacts.append(cp)
    docs=[dict(title=d.get('title') or d.get('documentType') or 'Official attachment',url=d['url'],kind='other',access='unknown') for d in tender.get('documents',[]) if str(d.get('url','')).startswith('https://')]
    item_descriptions=[str(i.get('description') or '') for i in tender.get('items',[]) if isinstance(i,dict)]
    description='\n'.join([tender.get('description') or '',*item_descriptions]).strip() or None
    r=notice(config,id=row['ocid'],title=tender.get('title') or tender.get('description'),description=description,buyer=buyer.get('name'),url=API+'record/'+row['ocid'],raw=row,published=tender.get('datePublished'),status=tender.get('status') or '; '.join(sorted(set(str(i.get('statusDetails') or i.get('status')) for i in tender.get('items',[])))),documents=docs,contacts=contacts,now=observed,locator='OCDS '+row['ocid'])
    r['raw_payload']['deadline_candidates_unverified']={'tenderPeriod':tender.get('tenderPeriod'),'enquiryPeriod':tender.get('enquiryPeriod')}
    r['raw_payload']['missing_fields'].append('OCDS tenderPeriod is not assumed to be bid-submission cutoff')
    return r

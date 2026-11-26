"""NYPA public RFQ feed used by rfp.nypa.gov; no account/admin/MSAL endpoints."""
from urllib.parse import urlencode
from collectors.common.records import notice
from collectors.common.pagination import Page
from collectors.common.runtime import SourceError
API='https://api.nypa.gov/rfq/v1/api/public/rfq/'
def listing(options,partition):return API+'Bids?'+urlencode(dict(statusText='Open',userAdminSite='',postDateMinimum=''))
def page(obj,url):
    if not isinstance(obj,list) or not all(isinstance(r,dict) and r.get('rfqid') for r in obj):raise SourceError('Invalid NYPA public bid array','parse_error')
    return Page(obj,None,len(obj))
def parse(config,row,observed):
    r=notice(config,id=row['rfqid'],title=row.get('rfqName'),buyer='New York Power Authority',url='https://rfp.nypa.gov/#/bid-details?rfqId='+str(row['rfqid']),raw=row,published=row.get('postDate'),status=row.get('statusText'),deadline=row.get('bidDueDate'),contacts=[dict(name=row.get('buyer'))] if row.get('buyer') else [],now=observed,locator='NYPA public RFQ list')
    return r
def enrich(fetch,r):
    url=API+'GetRfqDetails?'+urlencode(dict(rfqId=r['source_tender_id']));d=fetch.json(url)
    if not isinstance(d,dict) or d.get('rfqName')!=r['title_original']:raise SourceError('NYPA detail identity mismatch','parse_error')
    r['description_original']=d.get('abstract');r['raw_status']=d.get('statusText');r['raw_payload']['verified_detail']=d;r['raw_payload']['detail_url']=url;r['raw_payload']['detail_checked_at']=__import__('collectors.common.runtime',fromlist=['utc']).utc()
    # List supplies precise date/time; the detail may supply date only. Preserve both, flag conflicting dates.
    r['raw_payload']['detail_due_text']=d.get('bidDueDate')
    buyer=d.get('buyer')
    if isinstance(buyer,dict):
        from collectors.common.records import evidenced_contacts
        name=' '.join(str(buyer.get(k) or '') for k in ['firstName','lastName']).strip()
        cs,ev=evidenced_contacts([dict(name=name,email=buyer.get('email') or buyer.get('emailAddress'),phone=buyer.get('phone'))],url,'NYPA public tender buyer fields',r['fetched_at'])
        if cs:r['raw_payload']['contacts']=cs;r['raw_payload']['evidence'].extend(ev);r['contact_hints']=[dict(name=c['name'],email=c['email'],phone=c['phone'],source_url=url) for c in cs]
    return r

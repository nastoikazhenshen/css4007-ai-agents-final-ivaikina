"""Official operator's current invitation page. An opening date is not a submission deadline."""
import re,hashlib
from urllib.parse import urljoin
from lxml import html
from collectors.common.pagination import Page
from collectors.common.records import notice,text,EMAIL
from collectors.common.runtime import SourceError
URL='https://www.okanoganpud.org/work-with-us/rfp-bids/'
def listing(options,partition):return URL
def loader(fetch,url):
    ok,why=fetch.public_file_allowed(url)
    if not ok:raise SourceError(why,'auth_required')
    return fetch.get(url).text
def page(body,url):
    h=html.fromstring(body);rows=[];contact_values=[]
    for heading in h.xpath('//*[self::h2 or self::h3][normalize-space(.)="Questions"]'):
        for node in heading.itersiblings():
            if node.tag in {'h2','h3'}:break
            email=node.xpath('.//a[starts-with(@href,"mailto:")]/@href');phone=node.xpath('.//a[starts-with(@href,"tel:")]/@href');name=re.search(r'Questions should be directed to (.*?) at\b',node.text_content())
            if email or phone:contact_values.append(dict(name=name[1] if name else None,email=email[0][7:] if email else None,phone=phone[0][4:] if phone else None))
    # Only the operator's current-open section; no result/award archive.
    for heading in h.xpath('//*[self::h2 or self::h3 or self::h4][contains(.,"Current Open Bid")]'):
        nodes=[]
        for node in heading.itersiblings():
            if node.tag in {'h2','h3','h4'}:break
            nodes.append(node)
        for node in nodes:
            for a in node.xpath('.//a[@href]'):
                title=text(a.text_content());link=urljoin(url,a.get('href'))
                if not title or not link.lower().endswith('.pdf'):continue
                rows.append(dict(_source_key=hashlib.sha256(link.encode()).hexdigest()[:24],title=title,url=link,context=' '.join(text(n.text_content()) for n in nodes),procurement_questions_contact=contact_values))
    if not h.xpath('//*[contains(text(),"Current Open Bid")]'):raise SourceError('Current invitation section absent; cannot interpret as empty','parse_error')
    return Page(rows,None,len(rows))
def parse(config,row,observed):
    # Specific invitation document URL is retained, even when robots prevents downloading it.
    r=notice(config,id=row['_source_key'],title=row['title'],buyer='Okanogan County Public Utility District',url=row['url'],raw=row,description=row['context'],status='Listed in Current Open Bid Invitations; submission cutoff unconfirmed',documents=[dict(title=row['title'],url=row['url'],kind='tender_notice',access='restricted')],now=observed)
    r['raw_payload']['missing_fields'].append('Published bid opening/extension is not treated as submission cutoff; document download disallowed by robots.')
    from collectors.common.records import evidenced_contacts
    cs,ev=evidenced_contacts(row.get('procurement_questions_contact',[]),URL,'Official procurement-page Questions section, not a tender-specific appointed contact',observed)
    for c in cs:c['contact_type']='tender_department'
    r['raw_payload']['contacts']=cs;r['raw_payload']['evidence'].extend(ev)
    r['contact_hints']=[dict(name=c.get('name'),email=c.get('email'),phone=c.get('phone'),source_url=URL) for c in cs]
    r['raw_payload']['contact_scope']='procurement page Questions contact; tender-specific appointment unconfirmed'
    return r

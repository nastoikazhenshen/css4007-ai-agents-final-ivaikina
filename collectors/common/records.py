"""Lossless canonical raw-tender builders. No Active promotion or new central schema."""
import hashlib,json,re,unicodedata
from datetime import datetime
from lxml import html, etree
from .runtime import utc

PATTERN=re.compile(r'overhead|reconduct|\belectrif|transmi(?:ss|s)[aãioó]*[nõo]*\s+el[eé]ctr|\binsulators?\b|\bisoladores?\b|\baisladores?\b|\bizolat[oö]r|ferragen|herrajes|\bE[İI]H\b|havai|enerji\s+(?:iletim|nakil)|(?:linhas?|l[ií]neas?)\s+(?:a[eé]reas?\s+)?(?:de\s+)?transmi(?:ss|s)|\btransmission\s+(?:line|network|project)|(?:power|electric(?:al|ity)?)\s+transmission|substation|subesta|gardu|\bgrid\b|\btransmission and distribution\b|\bpower\s+(?:line|network)|electrical\s+network|linhas?\s+(?:de\s+)?(?:(?:alta|m[eé]dia|baixa)\s+tens[aã]o|distribui|el[eé]trica)|rede\s+(?:a[eé]rea|el[eé]trica|de\s+distribui)|l[ií]nea\s+(?:a[eé]rea|el[eé]ctrica|de\s+(?:distribuci|alta|media|baja))|สายส่ง|สายจำหน่าย|ลูกถ้วย',re.I)
EMAIL=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
def text(value):
    if not value:return ''
    try:return ' '.join(html.fromstring(str(value)).text_content().split())
    except (ValueError,TypeError,etree.ParserError):return str(value)
def candidate(*values):return bool(PATTERN.search(' '.join(text(v) for v in values if v)))
def evidenced_contacts(values,url,locator,now):
    contacts=[];evidence=[]
    for value in values:
        name=value.get('name');email=value.get('email');phone=value.get('phone') or value.get('telephone')
        if not any([name,email,phone]):continue
        excerpt=json.dumps(value,ensure_ascii=False);eid='ev_'+hashlib.sha256((url+locator+excerpt).encode()).hexdigest()[:20]
        evidence.append(dict(evidence_id=eid,source_url=url,locator=locator,original_excerpt=excerpt,observed_at=now))
        contacts.append(dict(name=name,email=email,phone=phone,department=value.get('department'),contact_type='tender_person' if name else 'tender_department',evidence_ids=[eid]))
    return contacts,evidence

def notice(config,*,id,title,buyer,url,raw,description=None,published=None,status=None,deadline=None,contacts=None,documents=None,now=None,locator='source record'):
    title=title.strip() or None if isinstance(title,str) else title
    description=description.strip() or None if isinstance(description,str) else description
    buyer=buyer.strip() or None if isinstance(buyer,str) else buyer
    now=now or utc();cs,ev=evidenced_contacts(contacts or [],url or config['official_homepage'],locator+' / contact fields',now)
    payload={'source_record':raw,'contacts':cs,'documents':[],'evidence':ev,'missing_fields':[],
             'url_kind':'official_source_record','commercial_classification':'candidate; central assessment required'}
    if not cs:payload['missing_fields'].append('contacts not published in parsed record')
    if not deadline:payload['missing_fields'].append('bid deadline not confirmed')
    return dict(source_tender_id=str(id) if id is not None else None,country_code=config['country_code'],title_original=title,
                description_original=description,buyer_original=buyer,listing_url=config['endpoints'][0]['url'],tender_url=url,
                published_on=published[:10] if published and re.match(r'^\d{4}-\d{2}-\d{2}',published) else None,
                deadline_text=deadline,raw_status=status,documents=documents or [],
                contact_hints=[dict(name=c.get('name'),email=c.get('email'),phone=c.get('phone'),source_url=url) for c in cs],raw_payload=payload,fetched_at=now)

def pdf_contacts(record,pdf_text,url,now=None):
    """PDF occurrences are leads, not attributed buyer contacts. Keep context for review."""
    now=now or utc();hints=[]
    for match in EMAIL.finditer(pdf_text):
        hints.append(dict(email=match[0],source_url=url,locator='PDF email occurrence',original_excerpt=pdf_text[max(0,match.start()-180):match.end()+180],observed_at=now,attribution='unconfirmed; may be bidder, issuer, or third party'))
    record['raw_payload'].setdefault('document_contact_candidates',[]).extend(hints)
    return record

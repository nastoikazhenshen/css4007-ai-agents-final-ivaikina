"""Attribute only explicitly designated clarification channels in official tender documents."""
import re
from .records import EMAIL,evidenced_contacts
from .runtime import utc

def procurement_pdf_contacts(record,content,url,*,locator='Official tender PDF explicitly designates clarification/challenge email'):
    # Merely finding @ in a document never proves the address belongs to the buyer.
    # The source must explicitly say where requests for clarification/challenges can be sent.
    pattern=re.compile(r'(?:pedido[s]? de esclarecimento|impugna[cç][aã]o)[\s\S]{0,300}(?:meios|e-mail|email|correio eletr[oô]nico)\s*[:：]?\s*$',re.I)
    existing={(c.get('email'),c.get('source_url')) for c in record['contact_hints']}
    # Published Turkish tender notices explicitly name the person for requesting the
    # tender document and further information. Require that instruction, not a footer email.
    flat=' '.join(content.split())
    designated=re.compile(r'ihale\s+dok[uü]man[ıi][^.;]{0,220}ihale\s+i[çc]eri[ğg]i[^.;]{0,120}bilgi\s+almak[^.;]{0,50}Sn\.\s*([^()]{2,100})\s*\(Tel\s*:\s*([+\d\s()-]{5,40}),\s*E-posta\s*:\s*('+EMAIL.pattern+r')\)\s*ile\s+irtibata',re.I)
    for match in designated.finditer(flat):
        name,phone,email=(v.strip() for v in match.groups())
        if (email,url) in existing:continue
        cs,ev=evidenced_contacts([dict(name=name,phone=phone,email=email,
            department='Official tender information/document-request contact',published_context=match[0])],
            url,locator+' / designated tender information contact',utc())
        record['raw_payload']['contacts'].extend(cs);record['raw_payload']['evidence'].extend(ev)
        record['contact_hints'].append(dict(name=name,email=email,phone=phone,source_url=url))
        record['raw_payload'].setdefault('contact_attribution',[]).append(dict(email=email,
            purpose='tender_information_and_documents',source_url=url,original_excerpt=match[0],
            method='explicit Turkish tender information/document instruction',locator=locator))
        record['raw_payload']['missing_fields']=[v for v in record['raw_payload'].get('missing_fields',[]) if v!='contacts not published in parsed record']
        existing.add((email,url))
    for m in EMAIL.finditer(content):
        before=' '.join(content[max(0,m.start()-500):m.start()].split())
        if not pattern.search(before) or (m[0],url) in existing:continue
        excerpt=content[max(0,m.start()-300):m.end()+100]
        cs,ev=evidenced_contacts([dict(email=m[0],department='Official tender clarification/challenge channel',published_context=excerpt)],url,locator,utc())
        record['raw_payload']['contacts'].extend(cs);record['raw_payload']['evidence'].extend(ev)
        record['contact_hints'].append(dict(name=None,email=m[0],phone=None,source_url=url))
        record['raw_payload'].setdefault('contact_attribution',[]).append(dict(email=m[0],purpose='tender_clarifications_or_challenges',source_url=url,original_excerpt=excerpt,method='explicit Portuguese instruction; not an arbitrary document email',locator=locator))
        record['raw_payload']['missing_fields']=[v for v in record['raw_payload'].get('missing_fields',[]) if v!='contacts not published in parsed record']
        existing.add((m[0],url))
    return record

"""Public CC BY 4.0 ministry tender pages, never interpreted as Open by page presence."""
from urllib.parse import urljoin,urlsplit
from lxml import html
from collectors.common.pagination import Page
from collectors.common.records import notice,text,EMAIL
from collectors.common.runtime import SourceError

def listing(options,partition):return partition

def page(body,url):
    if not isinstance(body,str):raise SourceError('Expected HTML','parse_error')
    h=html.fromstring(body)
    if not h.xpath('//h1'):raise SourceError('No tender-page H1','parse_error')
    nxt=h.xpath('//a[@rel="next"]/@href')
    return Page([dict(id=url,url=url,body=body)],urljoin(url,nxt[0]) if nxt else None,None)

def parse(config,row,observed):
    h=html.fromstring(row['body']);main=(h.xpath('//main') or [h])[0];title=text(h.xpath('//h1')[0].text_content());description=text(main.text_content());url=row['url']
    contacts=[]
    for a in main.xpath('.//a[starts-with(@href,"mailto:")]'):
        email=a.get('href')[7:].split('?')[0]
        if EMAIL.fullmatch(email):contacts.append(dict(email=email,department='Published on ministry project page; role unconfirmed'))
    docs=[]
    for a in main.xpath('.//a[@href]'):
        link=urljoin(url,a.get('href'))
        if link.startswith('https://') and urlsplit(link).path.lower().endswith(('.pdf','.docx','.xlsx','.zip')):docs.append(dict(title=text(a.text_content()) or 'Official attachment',url=link,kind='other',access='unknown'))
    dates=h.xpath('//meta[@property="article:published_time"]/@content')
    result=notice(config,id=url,title=title,description=description,buyer='Secretaría de Energía — page publisher; procuring entity requires confirmation',url=url,raw=dict(html_title=title,main_text=description),published=dates[0] if dates else None,contacts=contacts,documents=docs,now=observed,locator='HTML main')
    for c in result['raw_payload']['contacts']:c['contact_type']='generic_corporate'
    result['raw_payload']['missing_fields'].append('Tender page accessibility does not establish active status')
    return result

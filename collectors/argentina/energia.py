"""Argentina energy ministry public project pages and line list from official map."""
import re
from lxml import html
from collectors.planning.models import project
from collectors.common.records import text

def parse(config,pages,url):
    result=[]
    for number,content in enumerate(pages,1):
        # Only the labelled project list, not map station labels or prose fragments.
        start=content.find('AMBA II + STATCOM')
        if start<0:continue
        block=content[start:];block=re.split(r'Listado de obras',block)[0]
        for match in re.finditer(r'(?m)^((?:Alternativa (?:ESTE|OESTE) )?Línea[^\n]*(?:\n(?!Línea|Alternativa|ET |\d+\s*$|AMBA)[^\n]+)*)',block):
            title=' '.join(match[1].split())
            if len(title)<12:continue
            result.append(project(config,title,url,match[1],buyer='Secretaría de Energía — plan publisher; project procurer unconfirmed',locator=f'PDF page {number}; labelled project list',status='National prioritized expansion plan; no bid deadline'))
    return result

def parse_html(config,body,url):
    h=html.fromstring(body);titles=h.xpath('//h1');content=h.xpath('//main') or [h]
    if not titles:raise ValueError('Official project page has no H1')
    title=text(titles[0].text_content());excerpt=text(content[0].text_content())
    if not re.search(r'500\s*kV|alta tensi[oó]n|l[ií]nea',excerpt,re.I):return []
    return [project(config,title,url,excerpt[:12000],buyer='Secretaría de Energía — publisher',locator='HTML main / h1',status='Project information; tender status unconfirmed')]

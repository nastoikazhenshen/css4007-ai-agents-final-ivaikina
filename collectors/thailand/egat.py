"""EGAT public planning table parser. Fetching is gated pending permission/robots review."""
from lxml import html
from collectors.planning.models import project

def parse(config,body,url):
    h=html.fromstring(body);out=[]
    for table in h.xpath('//table'):
        for row in table.xpath('.//tr'):
            cells=[' '.join(c.text_content().split()) for c in row.xpath('./td')]
            if len(cells)<2 or not cells[0] or cells[0].lower()=='project':continue
            title=cells[0]
            if any(x in title.lower() for x in ['fire protection','submarine','substation improvement']):continue
            out.append(project(config,title,url,' | '.join(cells),buyer='EGAT — plan publisher',locator='HTML public projects table',completion=cells[1],status='Plan table; tender availability unconfirmed'))
    return out

"""ONS PAR/PEL public summary parser. Fetching awaits scoped reuse/access confirmation."""
import re
from lxml import html
from collectors.planning.models import project

def parse(config,body,url):
    h=html.fromstring(body);out={}
    for node in h.xpath('//p|//li'):
        text=' '.join(node.text_content().split())
        if not re.search(r'\bLT\b|linha de transmiss[aã]o',text,re.I):continue
        if not re.search(r'\bkV\b',text,re.I):continue
        # Preserve original summary as an unverified scope lead, not an invented named tender.
        title=text[:350];r=project(config,title,url,text,locator='Official PAR/PEL paragraph',status='Summary lead; project identity and procurement require verification');r['ohl_relevance']='summary lead; project identity requires verification';out[r['project_key']]=r
    return list(out.values())

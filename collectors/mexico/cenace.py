"""CENACE PDF project-caption parser. Offline until publication policy is confirmed."""
import re
from collectors.planning.models import project

def parse(config,pages,url):
    out={}
    for number,page in enumerate(pages,1):
        compact=' '.join(page.split())
        named=re.search(r'Se han programado los proyectos (.+?) como soluci[oó]n',compact,re.I)
        if named:
            names=named[1].split(' y ')
            for title in names:
                if 'entronque' not in title.casefold():continue
                key=title
                out[key]=project(config,title,url,compact[max(0,named.start()-200):named.end()+600],key=key,buyer='CENACE — plan publisher; procurer unconfirmed',locator=f'PDF page {number}; explicit programmed-project names',status='Referenced programmed project; current procurement requires verification')
        # Named project table captions only; do not turn every mention of a line into a project.
        for match in re.finditer(r'(?m)(?:Cuadro|Tabla)\s+(\d+(?:\.\d+)+)[.:]?\s*\n?([^\n]*(?:entronque|L[ií]nea|Red de Transmisi[oó]n)[^\n]*)',page,re.I):
            title=match[2].strip()
            if len(title)<15:continue
            out[match[1]]=project(config,title,url,match[0],key=match[1],buyer='CENACE — plan publisher',locator=f'PDF page {number}; table {match[1]}',status='Network expansion plan; no procurement status established')
    return list(out.values())

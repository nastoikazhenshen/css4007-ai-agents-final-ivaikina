"""Official Turkish investment programme project IDs, energy rows only."""
import re
from collectors.planning.models import project

def parse(config,pages,url):
    result={}
    for number,content in enumerate(pages,1):
        if 'ENERJİ' not in content:continue
        owner=re.search(r'PROJE SAHİBİ:\s*\n([^\n]+)',content)
        for match in re.finditer(r'(?m)^(\d{4}D\d{2}-\d+)\s*\n(.*?)(?=^\d{4}D\d{2}-\d+\s*$|\Z)',content,re.S|re.M):
            key,block=match.group(1,2)
            if not re.search(r'EİH|Enerji\s*\n?\s*İletim Hattı|Enerji Nakil|Havai',block,re.I):continue
            lines=block.splitlines();title=lines[0].strip()
            if len(lines)>1 and (re.search(r'380$|154$|–$|-$',title) or lines[1].strip().startswith(('EİH','Hattı'))):title+=' '+lines[1].strip()
            dates=re.search(r'\b(20\d{2})-(20\d{2})\b',block)
            result[key]=project(config,title,url,key+'\n'+block[:5000],key=key,buyer=owner[1].strip() if owner else None,locator=f'PDF page {number}; project {key}',completion=dates[0] if dates else None,status='Investment programme; may be in implementation; no open procurement implied')
    return list(result.values())

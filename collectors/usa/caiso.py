"""CAISO public Appendix H project tables; retain original labels and page locators."""
import re
from collectors.common.records import candidate
from collectors.planning.models import project

def parse(config,pages,url):
    result=[]
    for number,content in enumerate(pages,1):
        for block in re.split(r'(?m)^Name\s*\n',content)[1:]:
            bits=re.split(r'\nDescription\s*\n',block,maxsplit=1)
            if len(bits)!=2:continue
            title=' '.join(bits[0].split());description=re.split(r'\nType\s*\n',bits[1],maxsplit=1)[0]
            if not candidate(title,description):continue
            # Pure station/reactor equipment is excluded unless a line/reconductoring scope is explicit.
            if not re.search(r'\bline\b|circuit|reconduct|overhead|conductor',title+' '+description,re.I):continue
            end=re.search(r'Expected In\s*Service Date\s*\n(.*?)(?:\nInterim|\nProject Cost)',block,re.S)
            result.append(project(config,title,url,block[:5000],buyer=None,locator=f'PDF page {number}; Name / Description',completion=' '.join(end[1].split()) if end else None,status='Board approved investment plan; procurement not confirmed'))
    return result

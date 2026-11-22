"""World Bank project registry. Country-filtered plan candidates, separate from notices."""
from urllib.parse import urlencode,urlsplit,parse_qs
from collectors.common.pagination import Page
from collectors.common.runtime import SourceError
from collectors.common.records import candidate
from collectors.planning.models import project
API='https://search.worldbank.org/api/v2/projects'
def listing(cc,size=1000):return API+'?'+urlencode(dict(format='json',rows=size,os=0,countrycode_exact=cc,srt='id',order='asc'))
def page(obj,url):
    entries=obj.get('projects');total=obj.get('total')
    if not isinstance(entries,dict) or total is None:raise SourceError('Invalid World Bank projects envelope','parse_error')
    rows=[v for k,v in entries.items() if isinstance(v,dict) and v.get('id')];total=int(total)
    q={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()};offset=int(q['os']);size=int(q['rows']);q['os']=offset+size
    return Page(rows,API+'?'+urlencode(q) if offset+len(rows)<total else None,total)
def parse(config,row,observed):
    # Closed/dropped projects are retained as raw rows but are not a future-project candidate.
    if config['country_code'] not in row.get('countrycode',[]):raise SourceError('World Bank project country mismatch','parse_error')
    status=row.get('status')
    if status not in {'Active','Pipeline'}:return None
    scope=' '.join(str(row.get(k,'')) for k in ['project_name','p2_project_name','project_abstract','sector','sector1','sector2','sector3','theme1','theme2','borrower','impagency'])
    if not candidate(scope):return None
    id=row['id'];title=row.get('project_name') or row.get('p2_project_name')
    if not title:raise SourceError('Project title missing','parse_error')
    return project(config,title,'https://projects.worldbank.org/en/projects-operations/project-detail/'+id,scope[:12000],key=id,buyer=row.get('impagency') or row.get('borrower'),locator='World Bank project '+id,status=status)

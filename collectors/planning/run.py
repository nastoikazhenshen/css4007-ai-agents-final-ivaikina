"""Future-project staging only; deliberately not wired into central tender pipeline."""
import argparse,importlib,json
from pathlib import Path
import httpx,fitz
from collectors.common.config import load
from collectors.common.runtime import CollectorState,Fetcher,CurlTransport,SourceError,fingerprint,utc
from collectors.common.pagination import Pager
from collectors.planning.models import validate

MODULES={'us_caiso_plan':'collectors.usa.caiso','tr_sbb_plan':'collectors.turkey.sbb','ar_energia_plan':'collectors.argentina.energia'}
def execute(source,output,*,transport='httpx',max_pages=None):
    config=load(source);output=Path(output);output.mkdir(parents=True,exist_ok=True);state=CollectorState(output/'state');client=httpx.Client(transport=CurlTransport() if transport=='curl' else None,trust_env=transport!='curl',timeout=45,follow_redirects=False);fetch=Fetcher(config,state,client);rows=[];errors=[];docs=[];coverage=[];seen=0
    try:
        if not config['enabled'] or config['automation_permission']!='confirmed_permitted':raise SourceError('Autonomous collection disabled: permission not verified','auth_required')
        if source.endswith('_wb_projects'):
            from . import world_bank as adapter
            initial=adapter.listing(config['country_code']);pager=Pager(fetch,source,initial,adapter.page,lambda r:r.get('id'),max_pages=max_pages).collect();seen=len(pager.rows)
            if pager.error:errors.append(str(pager.error))
            coverage.append(dict(pages=pager.pages,received=seen,total=pager.total,complete=pager.complete))
            for row,observed in pager.rows:
                result=adapter.parse(config,row,observed)
                if result:result['source_record']=row;rows.append(result)
        else:
            adapter=importlib.import_module(MODULES[source])
            for endpoint in config['endpoints']:
                url=endpoint['url']
                if endpoint['purpose']=='document':
                    doc=dict(title=config['name'],url=url,kind='other',access='unknown');result=fetch.attachment(source,'plan',doc,output);docs.append(result)
                    if result['download_status']!='downloaded':errors.append(result['download_reason']);continue
                    with fitz.open(result['local_path']) as pdf:pages=[p.get_text() for p in pdf]
                    seen+=len(pages);projects=adapter.parse(config,pages,url)
                    for r in projects:r['documents']=[result]
                    rows.extend(projects);coverage.append(dict(document_url=url,pages=len(pages),complete=True))
                elif endpoint['purpose']=='detail':
                    ok,reason=fetch.public_file_allowed(url)
                    if not ok:raise SourceError(reason,'auth_required')
                    response=fetch.get(url);rows.extend(adapter.parse_html(config,response.content,url));seen+=1
        rows=list({r['project_key']:r for r in rows}.values())
        for row in rows:validate(row)
        changes=state.merge_records(source,rows,kind='planned_project')
        report=dict(source_id=source,record_type='planned_project_proposal',checked_at=utc(),records_seen=seen,future_project_candidates=len(rows),documents_downloaded=sum(x['download_status']=='downloaded' for x in docs),with_contacts=sum(bool(r['contact_hints']) for r in rows),coverage=coverage,errors=errors,changes=changes,sqlite_integrity=state.db.execute('PRAGMA integrity_check').fetchone()[0],central_integration='not connected; interface proposed for architecture approval')
        dest=output/source;dest.mkdir(exist_ok=True);(dest/'planned_projects.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n');(dest/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');return report
    except (SourceError,ValueError,KeyError) as e:
        report=dict(source_id=source,record_type='planned_project_proposal',checked_at=utc(),records_seen=seen,future_project_candidates=len(rows),documents_downloaded=sum(x['download_status']=='downloaded' for x in docs),with_contacts=0,coverage=coverage,errors=[str(e)],run_status='blocked_or_failed',central_integration='not connected')
        dest=output/source;dest.mkdir(exist_ok=True);(dest/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');return report
    finally:client.close();state.close()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--output',default='var/plans');p.add_argument('--transport',choices=['httpx','curl'],default='httpx');p.add_argument('--max-pages',type=int);a=p.parse_args();print(json.dumps(execute(a.source,a.output,transport=a.transport,max_pages=a.max_pages),ensure_ascii=False))
if __name__=='__main__':main()

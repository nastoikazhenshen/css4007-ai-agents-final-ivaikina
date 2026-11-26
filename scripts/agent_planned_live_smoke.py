#!/usr/bin/env python3
"""Read-only existing planning collectors -> separate future-project AI inventory.
No conversion to TenderRecord or confirmation of procurement-open status.
"""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.portfolio import report_row,summarize
from agent.tools_impl import TenderTools

PLAN_SOURCES={'US':'us_caiso_plan','TR':'tr_sbb_plan','AR':'ar_energia_plan'}

def run(country,output,max_runtime=240):
    source=PLAN_SOURCES[country]
    dest=output/country;dest.mkdir(parents=True,exist_ok=True)
    args=[sys.executable,'-m','collectors.planning.run','--source',source,
          '--output',str(dest/'capture'),'--max-pages','1']
    report={'mode':'new_live_planned_project_collection','country':country,'source':source,
            'checked_at':datetime.now(timezone.utc).isoformat(),'planned_project_rows':0,
            'planner_exit':None,'errors':[],'central_tender_records_created':0,
            'not_open_tenders':True,'records':[]}
    try:
        process=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=max_runtime)
        report['planner_exit']=process.returncode
        report['stdout_tail']=process.stdout[-700:]
        report['stderr_tail']=process.stderr[-350:]
    except subprocess.TimeoutExpired:
        report['planner_exit']='timed_out';report['errors'].append('planner_timeout')
    file=dest/'capture'/source/'planned_projects.json'
    if file.is_file():
        data=json.loads(file.read_text(encoding='utf-8'))
        if not isinstance(data,list):raise ValueError('Expected saved planned-project list')
        for proposal in data:
            if proposal.get('record_type')!='planned_project_proposal' or not proposal.get('not_an_open_tender'):
                raise ValueError('Invalid planned project proposal')
            tool=TenderTools(proposal)
            try:
                metadata=tool.inspect_tender()
                relevance=tool.assess_commercial_relevance()
                entry=report_row(proposal)
                entry['commercial_categories']=relevance.facts['categories']
                entry['tool_route']=metadata.facts['central_route']
                assert entry['route']=='planned_projects'
                report['records'].append(entry)
            finally:tool.close()
        report['planned_project_rows']=len(report['records'])
        report['metrics']=summarize(report['records'])
    (dest/'planned_agent_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--country',choices=PLAN_SOURCES,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=run(a.country,a.output)
    print(json.dumps({k:r[k] for k in ('country','source','planner_exit','planned_project_rows','central_tender_records_created')},ensure_ascii=False))

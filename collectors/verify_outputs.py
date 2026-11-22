"""Validate real saved CollectorRun files, then replay twice through existing central code.
This is offline verification of live-captured records, not an additional live request.
"""
import argparse,json,sqlite3,tempfile
from pathlib import Path
from collectors.common.runtime import CollectorState,schema
from integration.collector_validation import validate_run
from integration.candidate_store import CandidateInbox
from integration.pipeline import process_run

def verify(paths):
 runs=[json.loads(Path(p).read_text()) for p in paths];expected=set();errors=[]
 with tempfile.TemporaryDirectory() as d:
  state=CollectorState(Path(d)/'collector');inbox=CandidateInbox(Path(d)/'inbox.sqlite');checks=[]
  for run in runs:
   schema(run,'collector_run.schema.json');validate_run(run)
   for raw in run['tenders']:expected.add((run['source_id'],raw['source_tender_id'] or raw['tender_url']))
  for pass_number in [1,2]:
   totals=dict(new=0,changed=0,unchanged=0);processed=0
   for run in runs:
    try:
     result=process_run(run,inbox=inbox);processed+=len(result)
     delta=state.merge_records(run['source_id'],run['tenders'])
     for k,v in delta.items():totals[k]+=v
    except Exception as e:errors.append(dict(source=run['source_id'],error=str(e)))
   checks.append(dict(pass_number=pass_number,input_rows=sum(len(r['tenders']) for r in runs),central_rows_processed=processed,central_unique_rows=inbox.count(),collector_unique_rows=state.db.execute('select count(*) from collector_records').fetchone()[0],changes=totals,sqlite_integrity=state.db.execute('PRAGMA integrity_check').fetchone()[0],central_integrity=inbox.db.execute('PRAGMA integrity_check').fetchone()[0]))
  result=dict(run_files=len(runs),expected_source_identities=len(expected),checks=checks,errors=errors,schema_validation='passed',verification_type='offline replay of actual live captures; not a fresh network test')
  assert not errors and all(c['central_unique_rows']==len(expected) and c['sqlite_integrity']=='ok' and c['central_integrity']=='ok' for c in checks)
  assert checks[1]['changes']['new']==0
  state.close();inbox.close();return result
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('paths',nargs='+');p.add_argument('--report');a=p.parse_args();r=verify(a.paths);body=json.dumps(r,indent=2);print(body)
 if a.report:Path(a.report).write_text(body+'\n')

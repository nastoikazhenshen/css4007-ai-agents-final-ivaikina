"""Reanalyse saved live captures/PDFs without pretending to perform a new network request."""
import argparse,copy,hashlib,json,uuid
from pathlib import Path
from collectors.common.runtime import schema,utc,CollectorState
from collectors.common.opportunity import decide
from collectors.common.relevance import assess
from collectors.common.contact_attribution import procurement_pdf_contacts
from collectors.common.document_text import extract
from integration.collector_validation import validate_run
from integration.candidate_store import CandidateInbox
from integration.pipeline import process_run

def recheck(original,*,directory):
    schema(original,'collector_run.schema.json');run=copy.deepcopy(original);started=utc();source=run['source_id'];extracted=[]
    for r in run['tenders']:
        raw=r['raw_payload'];raw['reanalysis_origin_run_id']=original['run_id']
        if source=='mx_sidof' and raw.get('verified_detail'):
            from collectors.mexico.sidof import apply_detail
            apply_detail(r,raw['verified_detail'],raw['detail_url'],raw['detail_checked_at'])
        for d in raw.get('documents',[]):
            path=Path(d.get('local_path') or '/nonexistent-public_research-document')
            if not path.is_file() or not d.get('sha256'):continue
            if hashlib.sha256(path.read_bytes()).hexdigest()!=d['sha256']:
                raw.setdefault('reanalysis_errors',[]).append(dict(reason='Document hash mismatch',url=d['source_url']));continue
            for link in r['documents']:
                if link['url']==d['source_url']:link['access']='public'
            result=extract(path)
            d['text_extraction']=dict(parts=[{k:v for k,v in part.items() if k!='text'} for part in result['parts']],issues=result['issues'],complete=result['complete'])
            for part in result['parts']:
                content=part['text'];extracted.append(part)
                procurement_pdf_contacts(r,content,d['source_url'],locator=part['locator']+' explicitly designates clarification/challenge email')
                prior=assess(r['title_original'],r.get('description_original'));scope=assess(r['title_original'],content)
                identity_ok=source!='ar_contratar' or str(r['source_tender_id']) in content
                if identity_ok and scope['verdict']=='relevant' and (prior['verdict']=='needs_documents' or scope['overhead_or_product_explicit'] and not prior['overhead_or_product_explicit']):
                    if content[:200000] not in (r.get('description_original') or ''):
                        r['description_original']=(r.get('description_original') or '')+'\n'+content[:200000]
                    raw['specification_scope_evidence']=dict(url=d['source_url'],sha256=part['sha256'],parent_sha256=d['sha256'],locator=part['locator'],verdict=scope)
        if r.get('deadline_text'):raw['missing_fields']=[v for v in raw.get('missing_fields',[]) if v!='bid deadline not confirmed']
        raw['verification']=decide(r,source)
    run.update(run_id='reanalysis-'+uuid.uuid4().hex,started_at=started,finished_at=utc(),http_observations=[])
    run['coverage']['reason']='Offline reanalysis of saved live capture '+original['run_id']+'; original source detail observation times retained'
    run['metrics'].update(pages_requested=0,pages_received=0,records_seen=len(run['tenders']))
    schema(run,'collector_run.schema.json');validate_run(run)
    out=Path(directory);out.mkdir(parents=True,exist_ok=True);st=CollectorState(out/'state');inbox=CandidateInbox(out/'central_inbox.sqlite')
    try:
        for part in extracted:st.save_extracted(part['sha256'],part['needs_ocr'],part['text'],part['locator'])
        changes=st.merge_records(source,run['tenders']);processed=process_run(run,inbox=inbox)
        repeat=st.merge_records(source,run['tenders']);integrity=st.db.execute('PRAGMA integrity_check').fetchone()[0]
    finally:st.close();inbox.close()
    report=dict(source=source,execution_kind='offline_reanalysis_of_live_capture',origin_run_id=original['run_id'],schema_validation='passed',central_records=len(processed),records_emitted=len(run['tenders']),sqlite_integrity=integrity,changes=changes,repeat=repeat)
    dest=out/source;dest.mkdir(exist_ok=True);(dest/'collector_run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2)+'\n');(dest/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return run,report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',required=True);p.add_argument('--output',default='var/reanalysis');a=p.parse_args()
    for filename in a.runs:
        _,report=recheck(json.loads(Path(filename).read_text()),directory=a.output);print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()

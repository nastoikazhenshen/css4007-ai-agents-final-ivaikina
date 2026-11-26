"""Collector-side proof of current submission windows, independent of central Active routing."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit
from .relevance import assess,norm

def instant_bounds(value):
    """Unzoned source timestamps produce conservative UTC bounds, never an invented timezone."""
    if not value:return None
    try:d=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
    except (TypeError,ValueError):return None
    if d.tzinfo:return d.astimezone(timezone.utc),d.astimezone(timezone.utc),'source_offset'
    return (d-timedelta(hours=14)).replace(tzinfo=timezone.utc),(d+timedelta(hours=12)).replace(tzinfo=timezone.utc),'unresolved_timezone_conservative_bounds'

def decide(record,source_id,*,now=None):
    now=now or datetime.now(timezone.utc);raw=record['raw_payload'];scope=assess(record.get('title_original'),record.get('description_original'))
    if source_id=='tr_tredas' and raw.get('scope_document_review_required') and scope['reason'].startswith('No contextual evidence'):
        scope=dict(scope,verdict='needs_documents',reason='Generic official current-table heading; tender notice scope requires document review')
    raw['scope_assessment']=scope
    status=norm(record.get('raw_status'));detail=raw.get('verified_detail');checked=raw.get('detail_checked_at');proof_url=raw.get('detail_url');deadline=record.get('deadline_text');bounds=instant_bounds(deadline)
    result=dict(state='pending_verification',reason='Status/deadline not proven',source_status=record.get('raw_status'),deadline_original=deadline,scope=scope,checked_at=checked,proof_url=proof_url,deadline_earliest_utc=bounds[0].isoformat() if bounds else None,deadline_latest_utc=bounds[1].isoformat() if bounds else None,timezone_method=bounds[2] if bounds else None,central_active_decision='not assigned by collector')
    if source_id=='th_mea' and any(marker in (record.get('title_original') or '') for marker in ['ยกเลิกการประกวดราคา','ยกเลิกประกาศ','ประกาศยกเลิก']):
        result.update(state='closed_or_unavailable',reason='Explicit Thai cancellation marker in the official notice title');return result
    if any(w in status for w in ['cancel','anulad','revogad','suspens','awarded','contract award','nihai karar','iptal','pending selection']):result.update(state='closed_or_unavailable',reason='Explicit closed/cancelled/suspended/awarded source status');return result
    if bounds and bounds[1]<=now:result.update(state='closed',reason='Submission end is certainly past, including timezone uncertainty');return result
    past_day=instant_bounds(raw.get('bid_day_certainly_past'))
    if past_day and past_day[1]<=now:result.update(state='closed',reason='Official listed bid day is certainly past; exact closing time was not fabricated');return result
    if scope['verdict']=='excluded':result.update(state='excluded',reason=scope['reason']);return result
    if not isinstance(detail,dict) or not checked or not proof_url:return result
    expected_hosts={'br_pncp':'pncp.gov.br','br_compras':'pncp.gov.br','ar_contratar':'contratar.gob.ar','us_nypa':'api.nypa.gov','th_mea':'procurement.mea.or.th','mx_sidof':'sidof.segob.gob.mx'}
    parsed_url=urlsplit(proof_url)
    if parsed_url.scheme!='https' or parsed_url.hostname!=expected_hosts.get(source_id) or parsed_url.username or parsed_url.password:
        result['reason']='Proof URL is not on the expected official source';return result
    if source_id in {'br_pncp','br_compras','ar_contratar','th_mea'} and proof_url!=record.get('tender_url'):
        result['reason']='Proof does not reference the same official tender URL';return result
    try:observed=datetime.fromisoformat(checked.replace('Z','+00:00'))
    except (ValueError,TypeError):return result
    if observed.tzinfo is None or now-observed>timedelta(hours=24) or observed>now+timedelta(minutes=5):result['reason']='Detail proof is stale or has invalid time';return result
    is_open=False
    if source_id in {'br_compras','br_pncp'}:
        # API publication status alone is insufficient: verify both sides of the submission window,
        # correct purchase identity and no source result/cancellation/deletion.
        start=instant_bounds(detail.get('dataAberturaProposta'));end=instant_bounds(detail.get('dataEncerramentoProposta'))
        is_open=bool(detail.get('numeroControlePNCP')==record['source_tender_id'] and norm(detail.get('situacaoCompraNome'))=='divulgada no pncp' and detail.get('situacaoCompraId')==1 and not detail.get('contratacaoExcluida') and detail.get('valorTotalHomologado') is None and start and end and start[1]<=now<end[0] and deadline==detail.get('dataEncerramentoProposta'))
    elif source_id=='ar_contratar':
        is_open=bool(status=='publicado' and detail.get('process_number')==record['source_tender_id'] and detail.get('submission_deadline')==deadline and detail.get('deadline_basis')=='physical_documents_submission')
        result['deadline_basis']=detail.get('deadline_basis')
    elif source_id=='us_nypa':
        is_open=bool(status=='open' and detail.get('rfqName')==record.get('title_original'))
    elif source_id=='th_mea':
        sale_end=instant_bounds(detail.get('document_sale_end'))
        is_open=bool(status=='published bid schedule' and detail.get('source_id')==record['source_tender_id'] and detail.get('title')==record['title_original'] and detail.get('submission_deadline')==deadline)
        if sale_end and sale_end[1]<=now:
            result['reason']='Required document-sale/registration window already ended; new participation needs confirmation'
            return result
    if not bounds or bounds[0]<=now:result['reason']='A future submission deadline is not certain';return result
    if not is_open:result['reason']='Current source status/window not established';return result
    if scope['verdict']!='relevant':result['reason']='Open submission window, but target scope requires documents';result['submission_window_verified_open']=True;return result
    result.update(state='open_verified',reason='Fresh official detail confirms current submission window and target scope; contact not required for retention',submission_window_verified_open=True)
    return result

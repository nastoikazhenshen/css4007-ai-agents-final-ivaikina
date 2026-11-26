"""Targeted official Compras queries followed by live PNCP record/window verification."""
from . import compras,pncp
from collectors.common.runtime import SourceError,utc
from collectors.common.records import notice

def enrich(fetch,record):
    d=fetch.json(record['tender_url'])
    if not isinstance(d,dict) or d.get('numeroControlePNCP')!=record['source_tender_id']:raise SourceError('PNCP detail identity mismatch','parse_error')
    raw=record['raw_payload'];raw['verified_detail']=d;raw['detail_url']=record['tender_url'];raw['detail_checked_at']=utc()
    record['title_original']=d.get('objetoCompra') or record['title_original'];record['description_original']=d.get('informacaoComplementar') or record.get('description_original');record['raw_status']=d.get('situacaoCompraNome');record['deadline_text']=d.get('dataEncerramentoProposta')
    # Fresh header data must not erase relevant official item descriptions already received.
    for item in raw.get('line_item_scope_evidence',[]):
        text=item['original_excerpt']
        if text not in (record.get('description_original') or ''):
            record['description_original']=(record.get('description_original') or '')+'\n'+text
    return record

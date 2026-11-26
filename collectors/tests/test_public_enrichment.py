"""Synthetic controls for item discovery and bounded Office-document extraction."""
import io
import hashlib
import zipfile
from datetime import datetime

import httpx
import pytest

from collectors.brazil import pncp, items
from collectors.common.config import load
from collectors.common.document_text import extract
from collectors.common.runtime import CollectorState, Fetcher, SourceError
from collectors.opportunities import execute


def purchase(sequence=1, title='Fornecimento de materiais diversos'):
    return dict(numeroControlePNCP=f'12345678000190-1-{sequence:06d}/2026',
                anoCompra=2026, sequencialCompra=sequence,
                orgaoEntidade=dict(cnpj='12345678000190', razaoSocial='Synthetic Buyer'),
                objetoCompra=title, situacaoCompraId=1, situacaoCompraNome='Divulgada no PNCP',
                dataAberturaProposta='2020-01-01T00:00:00Z',
                dataEncerramentoProposta='2099-12-01T12:00:00Z',
                informacaoComplementar='Synthetic general procurement scope',
                valorTotalHomologado=None)


def item(number, text='Aisladores de vidrio para línea eléctrica aérea'):
    return dict(numeroItem=number, descricao=text, quantidade=1)


def record():
    return pncp.parse(load('br_pncp'), purchase(), '2026-11-22T00:00:00Z')


def test_item_pagination_saves_nonmatches_and_recovers_small_unspecified_quantity(tmp_path):
    state=CollectorState(tmp_path)
    requested=[]
    def respond(req):
        requested.append(str(req.url));n=int(req.url.params['pagina'])
        return httpx.Response(200,json=[item(1,'Synthetic paper'),item(2)] if n==1 else [item(3,'Synthetic chair')])
    client=httpx.Client(transport=httpx.MockTransport(respond))
    fetch=Fetcher(load('br_pncp'),state,client,sleep=lambda _:None)
    tender=record();result=items.collect(fetch,tender,page_size=2)
    assert result['complete'] and result['received']==3 and result['pages_received']==2
    assert len(tender['raw_payload']['line_item_scope_evidence'])==1
    assert tender['raw_payload']['line_item_scope_evidence'][0]['item_number']==2
    assert 'Aisladores' in tender['description_original']
    assert state.db.execute('SELECT count(*) FROM page_rows').fetchone()[0]==3
    assert not tender['contact_hints'] and len(requested)==2
    client.close();state.close()


@pytest.mark.parametrize('obj',[{},'invalid',{'itens':[], 'totalRegistros':'3'}, {'itens':[], 'numeroPagina':2}])
def test_invalid_item_envelopes_are_explicit_errors(obj):
    with pytest.raises(SourceError):
        items.page(obj,'https://pncp.gov.br/example?pagina=1&tamanhoPagina=2')


def test_partial_item_pagination_preserves_next_cursor_and_rows(tmp_path):
    state=CollectorState(tmp_path)
    client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=[item(1),item(2)])))
    tender=record();result=items.collect(Fetcher(load('br_pncp'),state,client,sleep=lambda _:None),tender,page_size=2,max_pages=1)
    assert not result['complete'] and result['received']==2
    assert 'pagina=2' in result['next_url'] and result['error']
    assert tender['title_original'] and tender['tender_url']
    client.close();state.close()


def test_repeated_item_page_is_not_complete(tmp_path):
    state=CollectorState(tmp_path)
    client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=[item(1),item(2)])))
    result=items.collect(Fetcher(load('br_pncp'),state,client,sleep=lambda _:None),record(),page_size=2)
    assert not result['complete'] and result['error']['code']=='parse_error'
    assert result['received']==2
    client.close();state.close()


def test_forbidden_item_route_stops_without_retry_or_card_loss(tmp_path):
    state=CollectorState(tmp_path);seen=[]
    def respond(req):
        seen.append(str(req.url));return httpx.Response(403,text='Forbidden')
    client=httpx.Client(transport=httpx.MockTransport(respond))
    tender=record();result=items.collect(Fetcher(load('br_pncp'),state,client,sleep=lambda _:None),tender)
    assert result['error']['code']=='http_403' and len(seen)==1
    assert tender['title_original'] and tender['tender_url']
    client.close();state.close()


def options(tmp_path, **extra):
    return dict(start='2026-01-01',end='2099-12-31',modalities=[4],
                output=str(tmp_path),document_limit=0,item_limit=1,**extra)


def test_hidden_item_survives_fresh_header_refresh_schema_and_repeat(tmp_path):
    row=purchase()
    def respond(req):
        if '/contratacoes/' in req.url.path:
            return httpx.Response(200,json=dict(data=[row],totalRegistros=1,totalPaginas=1,numeroPagina=1))
        if req.url.path.endswith('/itens'):return httpx.Response(200,json=[item(1)])
        return httpx.Response(200,json=row)
    client=httpx.Client(transport=httpx.MockTransport(respond))
    run,report=execute('br_pncp',options(tmp_path),client=client)
    assert report['item_enrichment']['recovered_candidates']==1
    assert len(run['tenders'])==report['central_records']==1
    tender=run['tenders'][0]
    assert 'Aisladores' in tender['description_original'] and not tender['contact_hints']
    assert report['schema_validation']=='passed' and report['sqlite_integrity']=='ok'
    _,repeat=execute('br_pncp',options(tmp_path),client=client)
    assert repeat['changes']==dict(new=0,changed=0,unchanged=1)
    client.close()


def test_bounded_item_queue_rotates_to_previously_uninspected_generic_headlines(tmp_path):
    rows=[purchase(1),purchase(2)];inspected=[]
    def respond(req):
        if '/contratacoes/' in req.url.path:
            return httpx.Response(200,json=dict(data=rows,totalRegistros=2,totalPaginas=1,numeroPagina=1))
        if req.url.path.endswith('/itens'):
            seq=int(req.url.path.split('/')[-2]);inspected.append(seq)
            return httpx.Response(200,json=[item(1,'Synthetic paper' if seq==1 else 'Overhead power line construction')])
        return httpx.Response(200,json=rows[int(req.url.path.split('/')[-1])-1])
    client=httpx.Client(transport=httpx.MockTransport(respond))
    first,report=execute('br_pncp',options(tmp_path),client=client)
    assert report['minimum_field_notices_saved']==2 and not first['tenders']
    second,report=execute('br_pncp',options(tmp_path),client=client)
    assert inspected==[1,2] and len(second['tenders'])==1
    assert report['item_enrichment']['eligible']==2
    client.close()


def test_document_metadata_is_saved_with_zero_download_budget(tmp_path):
    row=purchase(title='Construction overhead transmission line')
    def respond(req):
        if '/contratacoes/' in req.url.path:
            return httpx.Response(200,json=dict(data=[row],totalRegistros=1,totalPaginas=1,numeroPagina=1))
        if req.url.path.endswith('/arquivos'):
            return httpx.Response(200,json=[dict(titulo='Synthetic terms',url='https://pncp.gov.br/example.pdf')])
        return httpx.Response(200,json=row)
    client=httpx.Client(transport=httpx.MockTransport(respond))
    opt=options(tmp_path,document_list_limit=1);opt['item_limit']=0
    run,report=execute('br_pncp',opt,client=client)
    assert run['tenders'][0]['documents'][0]['url'].endswith('example.pdf')
    assert not run['tenders'][0]['raw_payload']['documents']
    assert report['document_enrichment']['listings_attempted']==1
    assert report['document_enrichment']['downloads_attempted']==0
    client.close()


def docx_bytes(text='Synthetic overhead transmission line specification'):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>office@example.test</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return stream.getvalue()


def xlsx_bytes(formula=False):
    from openpyxl import Workbook
    book=Workbook();sheet=book.active;sheet.title='Synthetic BOQ'
    sheet.append(['Description','Amount','Date'])
    sheet.append(['Overhead power line construction',1,datetime(2026,12,1)])
    if formula:sheet['D2']='=1+1'
    stream=io.BytesIO();book.save(stream);book.close();return stream.getvalue()


def test_docx_table_text_and_original_file_are_preserved(tmp_path):
    path=tmp_path/'download.bin';path.write_bytes(docx_bytes())
    result=extract(path)
    assert result['complete'] and result['parts'][0]['format']=='docx'
    assert 'transmission line' in result['parts'][0]['text']
    assert 'office@example.test' in result['parts'][0]['text'] and path.is_file()


def test_xlsx_preserves_sheet_cell_locations_and_dates_without_minimum_quantity(tmp_path):
    path=tmp_path/'download.zip';path.write_bytes(xlsx_bytes())
    result=extract(path);part=result['parts'][0]
    assert result['complete'] and part['format']=='xlsx'
    assert 'Synthetic BOQ' in part['text'] and 'A2: Overhead' in part['text']
    assert 'C2: 2026-12-01' in part['text'] and part['sha256']


def test_xlsx_formula_is_never_evaluated(tmp_path):
    path=tmp_path/'sheet.xlsx';path.write_bytes(xlsx_bytes(formula=True))
    result=extract(path)
    assert '[Formula preserved; not evaluated] =1+1' in result['parts'][0]['text']
    assert not result['complete'] and result['issues'][0]['reason']=='formulas_not_evaluated'


def test_public_zip_includes_office_files_without_filesystem_unpacking(tmp_path):
    path=tmp_path/'attachments.zip'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('docs/spec.docx',docx_bytes());z.writestr('docs/boq.xlsx',xlsx_bytes())
    result=extract(path)
    assert {p['format'] for p in result['parts']}=={'docx','xlsx'}
    assert all(p['locator'].startswith('ZIP member: docs/') for p in result['parts'])
    assert not (tmp_path/'docs').exists()


def test_office_xml_entity_declaration_is_rejected_in_utf16(tmp_path):
    path=tmp_path/'untrusted.docx'
    xml='<?xml version="1.0" encoding="utf-16"?><!DOCTYPE document [<!ENTITY secret SYSTEM "file:///etc/passwd">]><document>&secret;</document>'
    with zipfile.ZipFile(path,'w') as z:z.writestr('word/document.xml',xml.encode('utf-16'))
    result=extract(path)
    assert not result['parts'] and result['issues'][0]['reason']=='xml_dtd_or_entity_unsupported'


def test_office_member_and_text_budgets_are_explicit(tmp_path):
    path=tmp_path/'sheet.xlsx';path.write_bytes(xlsx_bytes())
    result=extract(path,max_members=1)
    assert not result['parts'] and result['issues'][0]['reason']=='member_budget'
    result=extract(path,max_text_chars=30)
    assert len(result['parts'][0]['text'])<=30 and not result['complete']


def test_peru_original_title_and_nested_item_description_are_both_retained():
    from collectors.peru import oece
    row=dict(ocid='synthetic-ocid',compiledRelease=dict(tender=dict(
        title='Synthetic framework procurement',description='Official general description',
        status='active',items=[dict(description='Overhead power line reconstruction')]),buyer=dict(name='Synthetic Buyer')))
    tender=oece.parse(load('pe_oece'),row,'2026-11-22T00:00:00Z')
    assert tender['title_original']=='Synthetic framework procurement'
    assert 'Overhead power line' in tender['description_original']
    assert tender['raw_status']=='active' and not tender['deadline_text']


def test_peru_forbidden_first_month_stops_other_months_and_validates_run(tmp_path):
    from collectors.run import execute as raw_execute
    requested=[]
    def respond(req):
        requested.append(str(req.url));return httpx.Response(403,text='Forbidden')
    client=httpx.Client(transport=httpx.MockTransport(respond))
    run,report=raw_execute('pe_oece',dict(start='2026-10-01',end='2026-11-22',
        mode='full',output=str(tmp_path),document_limit=0),client=client,sleep=lambda _:None)
    assert len(requested)==1 and run['run_status']=='blocked'
    assert report['schema_validation']=='passed' and report['minimum_field_notices_saved']==0
    client.close()


def test_tredas_generic_official_notice_is_read_before_scope_exclusion(tmp_path):
    import fitz
    from collectors.turkey import tredas
    doc=fitz.open();sheet=doc.new_page();sheet.insert_text((50,50),'Synthetic overhead power line reconstruction scope');pdf=doc.tobytes();doc.close()
    def respond(req):
        if req.url.path=='/modules/load/bids':
            return httpx.Response(200,json=dict(data=[dict(id=1,ihale_title='Synthetic general framework',
                publish_file_url='<a href="/synthetic.pdf">Notice</a>',statu='Published')],recordsFiltered=1))
        if req.url.path.endswith('/robots.txt'):return httpx.Response(200,text='User-agent: *\nAllow: /')
        if req.url.path.endswith('.pdf'):return httpx.Response(200,content=pdf,headers={'content-type':'application/pdf'})
        return httpx.Response(200,text='<html>Public current tenders</html>')
    client=httpx.Client(transport=httpx.MockTransport(respond))
    opt=options(tmp_path);opt['document_limit']=1
    run,report=execute('tr_tredas',opt,client=client)
    assert len(run['tenders'])==1 and report['minimum_field_notices_saved']==1
    record=run['tenders'][0]
    assert record['raw_payload']['specification_scope_evidence']['verdict']['verdict']=='relevant'
    assert record['raw_payload']['verification']['state']=='pending_verification'
    assert 'overhead power line' in record['description_original']
    client.close()


def test_offline_recheck_uses_docx_without_refreshing_source_status_time(tmp_path):
    from collectors.common.runtime import utc
    from collectors.recheck import recheck
    tender=pncp.parse(load('br_pncp'),purchase(title='Substation construction'),utc())
    original_checked=utc()
    tender['raw_payload'].update(verified_detail=purchase(title='Substation construction'),
        detail_url=tender['tender_url'],detail_checked_at=original_checked)
    body=docx_bytes('Synthetic overhead power line construction. Pedido de esclarecimento por email: office@example.test')
    path=tmp_path/'spec.docx';path.write_bytes(body);url='https://pncp.gov.br/synthetic-spec.docx'
    tender['documents']=[dict(title='Synthetic specification',url=url,kind='specification',access='public')]
    tender['raw_payload']['documents']=[dict(local_path=str(path),sha256=hashlib.sha256(body).hexdigest(),source_url=url)]
    original=dict(schema_version='1.0.0',run_id='synthetic-office-recheck',source_id='br_pncp',country_code='BR',
        started_at=utc(),finished_at=utc(),run_status='success',
        coverage=dict(verified_complete=True,reason='Synthetic control',date_from=None,date_to=None,next_cursor=None),
        metrics=dict(pages_requested=1,pages_received=1,records_seen=1,records_emitted=1,errors_count=0),
        http_observations=[],errors=[],tenders=[tender])
    run,report=recheck(original,directory=tmp_path/'rechecked')
    found=run['tenders'][0]
    assert found['raw_payload']['detail_checked_at']==original_checked
    assert found['raw_payload']['documents'][0]['text_extraction']['parts'][0]['format']=='docx'
    assert found['raw_payload']['specification_scope_evidence']['parent_sha256']==hashlib.sha256(body).hexdigest()
    assert found['contact_hints'][0]['email']=='office@example.test'
    assert 'DOCX' in found['raw_payload']['contact_attribution'][0]['locator']
    assert not run['http_observations'] and report['execution_kind']=='offline_reanalysis_of_live_capture'


def test_changed_item_updates_same_card_and_creates_history(tmp_path):
    version=['Overhead power line construction']
    row=purchase()
    def respond(req):
        if '/contratacoes/' in req.url.path:
            return httpx.Response(200,json=dict(data=[row],totalRegistros=1,totalPaginas=1,numeroPagina=1))
        if req.url.path.endswith('/itens'):return httpx.Response(200,json=[item(1,version[0])])
        return httpx.Response(200,json=row)
    client=httpx.Client(transport=httpx.MockTransport(respond))
    execute('br_pncp',options(tmp_path),client=client)
    version[0]='Reconstruction of overhead transmission line'
    _,report=execute('br_pncp',options(tmp_path),client=client)
    assert report['changes']==dict(new=0,changed=1,unchanged=0)
    state=CollectorState(tmp_path/'state')
    assert state.db.execute('SELECT count(*) FROM collector_records').fetchone()[0]==1
    assert state.db.execute('SELECT count(*) FROM collector_history').fetchone()[0]==2
    state.close();client.close()


def test_turkish_document_contact_requires_explicit_tender_information_instruction():
    from collectors.common.contact_attribution import procurement_pdf_contacts
    tender=record();url='https://cms.tredas.com.tr/synthetic.pdf'
    procurement_pdf_contacts(tender,'Supplier contact: Synthetic Vendor, office@example.test',url)
    assert not tender['contact_hints']
    text='İhale dokümanı talep etmek ve ihale içeriği hakkında detaylı bilgi almak üzere Sn. Synthetic Person (Tel: 0000 000 00 00, E-posta: office@example.test) ile irtibata geçilebilir.'
    procurement_pdf_contacts(tender,text,url,locator='PDF')
    procurement_pdf_contacts(tender,text,url,locator='PDF')
    assert len(tender['contact_hints'])==1
    assert tender['contact_hints'][0]['name']=='Synthetic Person'
    assert tender['raw_payload']['contact_attribution'][0]['purpose']=='tender_information_and_documents'


def test_rate_limited_listing_pauses_document_metadata_and_item_requests(tmp_path):
    row=purchase(title='Overhead power line construction');seen=[]
    def respond(req):
        seen.append(req.url.path)
        if req.url.params.get('pagina')=='1':
            return httpx.Response(200,json=dict(data=[row],totalRegistros=2,totalPaginas=2,numeroPagina=1))
        return httpx.Response(429,headers={'Retry-After':'120'})
    client=httpx.Client(transport=httpx.MockTransport(respond))
    opts=options(tmp_path,document_list_limit=100);opts['document_limit']=10
    run,report=execute('br_pncp',opts,client=client)
    assert len(seen)==2 and run['run_status']=='partial'
    assert report['document_enrichment']['listings_attempted']==0
    assert report['item_enrichment']['attempted']==0
    assert 'retry_delay_seconds=120' in run['errors'][0]['message']
    assert report['minimum_field_notices_saved']==1
    client.close()


def test_overlapping_oece_pages_continue_without_claiming_full_coverage(tmp_path):
    from collectors.common.pagination import Pager,Page
    state=CollectorState(tmp_path)
    def respond(req):
        if req.url.path.endswith('/1'):return httpx.Response(200,json=[{'ocid':'one'},{'ocid':'two'}])
        return httpx.Response(200,json=[{'ocid':'two'},{'ocid':'three'}])
    client=httpx.Client(transport=httpx.MockTransport(respond))
    fetch=Fetcher(load('pe_oece'),state,client,sleep=lambda _:None)
    def parse(obj,url):return Page(obj,url[:-1]+'2' if url.endswith('/1') else None)
    initial='https://contratacionesabiertas.oece.gob.pe/synthetic/1'
    pager=Pager(fetch,'synthetic-oece',initial,parse,lambda r:r['ocid'],allow_overlap=True).collect()
    assert len(pager.rows)==3 and pager.pages==2 and pager.received_rows==4
    assert pager.overlap_rows==1 and not pager.complete and pager.error
    assert state.db.execute('SELECT count(*) FROM raw').fetchone()[0]==2
    client.close();state.close()


def test_exhausted_source_retry_budget_prevents_further_origin_requests_in_same_run(tmp_path):
    state=CollectorState(tmp_path);seen=[]
    def respond(req):
        seen.append(str(req.url));return httpx.Response(503)
    client=httpx.Client(transport=httpx.MockTransport(respond))
    fetch=Fetcher(load('br_pncp'),state,client,sleep=lambda _:None)
    with pytest.raises(SourceError):fetch.get('https://pncp.gov.br/synthetic-one')
    with pytest.raises(SourceError):fetch.get('https://pncp.gov.br/synthetic-two')
    assert len(seen)==3 and fetch.requests==3
    assert len(fetch.observations)==3
    client.close();state.close()

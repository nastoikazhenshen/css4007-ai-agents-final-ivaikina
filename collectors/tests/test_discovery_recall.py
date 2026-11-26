import copy,io,json,sqlite3
from datetime import datetime,timezone
from pathlib import Path
import httpx,pytest
from collectors.common.config import load
from collectors.common.discovery import remember,export
from collectors.common.records import notice
from collectors.common.relevance import assess,norm
from collectors.common.runtime import CollectorState
from collectors.opportunities import execute

@pytest.mark.parametrize('title',[
 'ซื้อSteel Crossarm ขนาด 125 x 65 x 6 มม.',
 'ซื้อParallel Groove Clamp Two Bolt Type for Aluminum Conductor',
 'Spool Insulator Class 53-2 for 600 V',
 'Pin Post Insulator Class 56/57-2',
 'Full Tension Sleeves for Aluminum Conductor',
 'Reabilitação de alimentadores 13,8 kV',
 'Mejoramiento del servicio de energía eléctrica rural',
 'OG/AG şebeke tesisi yapım işi',
 'Rebuild feeder and replace wood poles'])
def test_broader_network_wording_is_not_lost(title):
    assert assess(title)['verdict'] in {'relevant','needs_documents'}
    assert assess(title)['confirmed_product_fit'] is False

@pytest.mark.parametrize('title',[
 'Printer with automatic document feeder and network installation',
 'Impressora multifuncional, instalação em rede e alimentador automático de documentos',
 'Door hardware and glass doors',
 'Overhead scanner',
 'Hydraulic Cable Cutter for transmission line department',
 'Underground cable-only line construction'])
def test_broader_recall_does_not_promote_clear_noise(title):assert assess(title)['verdict']=='excluded'

def test_mixed_script_normalization_keeps_thai_words():
    s=norm('ซื้อSteel Crossarm และสายส่ง')
    assert 'ซื้อ steel' in s and 'สายส่ง' in s

def test_minimum_fields_saved_before_relevance_without_contact_or_deadline(tmp_path):
    st=CollectorState(tmp_path);cfg=load('br_pncp')
    r=notice(cfg,id='synthetic',title='Generic framework 2026',buyer=None,url='https://pncp.gov.br/synthetic',raw={})
    key=remember(st,cfg,r);assert key
    assert not r['contact_hints'] and not r['deadline_text']
    rows=export(st,'br_pncp',{key},tmp_path/'inventory.csv')
    assert rows[0]['relevance']=='unclassified' and rows[0]['not_an_active_claim']
    remember(st,cfg,r);assert st.db.execute('select count(*) from discovery_notices').fetchone()[0]==1
    assert st.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';st.close()

@pytest.mark.parametrize('field,value',[('title_original',None),('tender_url',None),('tender_url','https://other.test/card'),('country_code','US')])
def test_missing_or_nonofficial_core_fields_not_fabricated(tmp_path,field,value):
    st=CollectorState(tmp_path);cfg=load('br_pncp');r=notice(cfg,id='synthetic',title='Line',buyer=None,url='https://pncp.gov.br/synthetic',raw={});r[field]=value
    assert remember(st,cfg,r) is None;st.close()

def test_document_budget_is_shared_and_unknown_scopes_are_inspected_first(tmp_path):
    import fitz
    def row(seq,title):return dict(numeroControlePNCP=f'12345678000190-1-{seq:06d}/2026',anoCompra=2026,sequencialCompra=seq,orgaoEntidade={'cnpj':'12345678000190','razaoSocial':'Synthetic Buyer'},objetoCompra=title,situacaoCompraId=1,situacaoCompraNome='Divulgada no PNCP',dataAberturaProposta='2020-01-01T00:00:00Z',dataEncerramentoProposta='2099-12-01T12:00:00Z')
    rows=[row(1,'Construction overhead transmission line'),row(2,'Substation construction'),row(3,'Printer with document feeder')]
    doc=fitz.open();page=doc.new_page();page.insert_text((50,50),'Synthetic specification control only');pdf=doc.tobytes();doc.close();downloads=[]
    def reply(req):
        path=req.url.path
        if '/contratacoes/' in path:return httpx.Response(200,json=dict(data=rows,totalRegistros=3,totalPaginas=1,numeroPagina=1))
        if path.endswith('/robots.txt'):return httpx.Response(200,text='User-agent: *\nAllow: /')
        if path.endswith('.pdf'):downloads.append(path);return httpx.Response(200,content=pdf,headers={'content-type':'application/pdf'})
        if path.endswith('/arquivos'):
            seq=int(path.split('/')[-2]);return httpx.Response(200,json=[dict(titulo=f'Edital {n}',url=f'https://pncp.gov.br/spec-{seq}-{n}.pdf') for n in range(1,5)])
        seq=int(path.split('/')[-1]);return httpx.Response(200,json=rows[seq-1])
    c=httpx.Client(transport=httpx.MockTransport(reply))
    run,report=execute('br_pncp',dict(start='2026-01-01',end='2099-12-31',modalities=[4],output=str(tmp_path),document_limit=2),client=c)
    assert downloads==['/spec-2-1.pdf','/spec-1-1.pdf']
    assert report['minimum_field_notices_saved']==3 and len(run['tenders'])==2
    assert report['central_records']==2 and not report['central_error']
    assert (tmp_path/'br_pncp/discovery_inventory.csv').exists();c.close()

def test_zip_pdf_members_are_read_without_unpacking_to_disk(tmp_path):
    import fitz,zipfile,hashlib
    from collectors.common.document_text import extract
    d=fitz.open();p=d.new_page();p.insert_text((50,50),'Synthetic overhead transmission line specification');body=d.tobytes();d.close()
    archive=tmp_path/'public.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('public/spec.pdf',body);z.writestr('later.rar',b'unsupported')
    r=extract(archive)
    assert len(r['parts'])==1 and 'transmission line' in r['parts'][0]['text']
    assert r['parts'][0]['sha256']==hashlib.sha256(body).hexdigest()
    assert r['parts'][0]['locator']=='ZIP member: public/spec.pdf'
    assert not r['complete'] and r['issues'][0]['reason']=='unsupported_member_format'
    assert not (tmp_path/'public').exists()

def test_zip_unsafe_path_and_expansion_budget_are_not_followed(tmp_path):
    import zipfile
    from collectors.common.document_text import extract
    archive=tmp_path/'hostile.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('../outside.pdf',b'%PDF fake');z.writestr('oversize.pdf',b'%PDF'+b'x'*1000)
    r=extract(archive,max_member_bytes=100)
    assert not r['parts'] and {i['reason'] for i in r['issues']}=={'unsafe_member_path','archive_expansion_budget'}
    assert not (tmp_path.parent/'outside.pdf').exists()

def test_scanned_pdf_requires_ocr_without_discarding_original(tmp_path):
    import fitz
    from collectors.common.document_text import extract
    p=tmp_path/'scan.pdf';d=fitz.open();d.new_page();d.save(p);d.close()
    r=extract(p);assert r['parts'][0]['needs_ocr'] and not r['complete']
    assert r['issues'][0]['reason']=='ocr_required' and p.is_file()

def test_corrupt_archive_records_reason(tmp_path):
    from collectors.common.document_text import extract
    p=tmp_path/'bad.zip';p.write_bytes(b'PK corrupt archive')
    r=extract(p);assert not r['complete'] and r['issues'][0]['reason']=='invalid_or_unsupported_archive'

def test_recovered_thai_component_cancellation_is_not_an_open_lead():
    from collectors.common.opportunity import decide
    r=notice(load('th_mea'),id='synthetic',title='ซื้อPin Post Insulator Class 56/57-2 **ยกเลิกการประกวดราคา**',buyer='Synthetic utility',url='https://procurement.mea.or.th/synthetic',raw={},status='Published bid schedule',deadline='2099-12-01T12:00:00+07:00')
    assert decide(r,'th_mea')['state']=='closed_or_unavailable'

def test_rule_comparison_does_not_mix_changed_inputs_with_recovered_rows(tmp_path):
    from collectors.recall_audit import saved_notices,snapshot,audit
    database=tmp_path/'raw.sqlite'
    row=dict(numeroControlePNCP='12345678000190-1-000001/2026',anoCompra=2026,sequencialCompra=1,orgaoEntidade={'cnpj':'12345678000190','razaoSocial':'Synthetic Buyer'},objetoCompra='Synthetic overhead line rebuild')
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE page_rows(scope TEXT,payload TEXT,observed TEXT)')
        db.execute('INSERT INTO page_rows VALUES (?,?,?)',('br_pncp:synthetic',json.dumps(row),'2026-11-22T00:00:00Z'))
    notices,_=saved_notices(database,'br_pncp');before=snapshot(notices,lambda *args:dict(verdict='excluded'))
    report,_,recovered=audit(database,'br_pncp',baseline=before,inventory_directory=tmp_path/'inventory')
    assert report['comparable_rows']==1 and report['newly_flagged_candidates']==1 and len(recovered)==1
    assert report['new_network_requests']==0 and report['not_new_active_tenders']
    before[next(iter(before))]['raw_hash']='different input'
    changed,_,recovered=audit(database,'br_pncp',baseline=before)
    assert changed['comparable_rows']==0 and changed['changed_input_rows']==1
    assert not recovered and changed['newly_flagged_candidates']==0

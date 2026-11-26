#!/usr/bin/env python3
"""Real API A-E benchmark. No key -> explicit NOT_RUN, never faked success."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.openrouter_client import FREE_MODELS,OpenRouterClient,MissingKey,ModelUnavailable,ProviderError
from agent.orchestrator import BoundedOrchestrator,AgentLimits
from agent.tools_impl import TOOL_SPECS, TenderTools

SYSTEM="""Extract exact tender facts. Return JSON with fields title, deadline_date,
 deadline_time, timezone, contact_name, contact_email, original_excerpts.
 Each excerpt MUST come from supplied content; otherwise use null. You must
 distinguish registration closing time from bid submission deadline. No guesses."""

# Labeled synthetic controls: not represented as real tender cards.
MULTILINGUAL=[
 {"language":"English","text":"CONTROL EXAMPLE: Overhead line works. Bids close 18 December 2026 at 14:00 UTC. Contact: Jane Example, bids@example.org.","date":"2026-12-18"},
 {"language":"Portuguese","text":"EXEMPLO SINTÉTICO: Construção de linha de transmissão. Envio das propostas até 18 de dezembro de 2026 às 14h, horário de Brasília. Contato: Maria Exemplo, propostas@example.org.","date":"2026-12-18"},
 {"language":"Spanish","text":"EJEMPLO SINTÉTICO: Licitación de aisladores. Presentación de ofertas hasta el 18 de diciembre de 2026 a las 14:00, hora de Lima. Contacto: Ana Ejemplo, compras@example.org.","date":"2026-12-18"},
 {"language":"Turkish","text":"SENTETİK ÖRNEK: Enerji nakil hattı ihalesi. Son teklif verme tarihi 18 Aralık 2026 saat 14:00 (Türkiye saati). İrtibat: Ali Örnek, ihale@example.org.","date":"2026-12-18"},
 {"language":"Thai","text":"ตัวอย่างสังเคราะห์: ประกวดราคาก่อสร้างสายส่งไฟฟ้า ยื่นข้อเสนอภายในวันที่ 18 ธันวาคม 2569 เวลา 14:00 น. เวลาประเทศไทย ติดต่อ: ตัวอย่าง ผู้รับผิดชอบ example@example.org","date":"2026-12-18"},
 {"language":"Indonesian","text":"CONTOH SINTETIS: Pengadaan isolator. Batas pemasukan penawaran tanggal 18 Desember 2026 pukul 14.00 WIB. Kontak: Budi Contoh, tender@example.org.","date":"2026-12-18"},
]


def _obtain_response(client,model,messages,**opts):
    t=time.monotonic()
    try:
        c=client.complete(model,messages,max_tokens=opts.get('max_tokens',700),
                          image=opts.get('image',False),tools=opts.get('tools'))
        raw=c.message.get('content') or ''
        found={}
        try:
            clean=raw.strip()
            if clean.startswith('```'):clean=clean.split('\n',1)[1].rsplit('```',1)[0]
            found=json.loads(clean)
        except (ValueError,TypeError,IndexError):pass
        return {'status':'api_success','elapsed_seconds':c.elapsed_seconds,'usage':c.usage,
                'cost_reported_usd':c.cost_usd,'model_returned':c.model,
                'native_tool_calls':len(c.message.get('tool_calls') or []),
                'tool_call_names':[x.get('function',{}).get('name') for x in c.message.get('tool_calls') or []],
                'answer':found if isinstance(found,dict) else None,
                'text_excerpt':raw[:600] if not found else None,
                'finish_reason':c.finish_reason}
    except (MissingKey,ModelUnavailable,ProviderError) as exc:
        return {'status':'blocked','error_type':type(exc).__name__,'error':str(exc),
                'elapsed_seconds':round(time.monotonic()-t,3)}


def _img(path):
    import base64,mimetypes
    mime=mimetypes.guess_type(path)[0]
    if mime not in {'image/png','image/jpeg','image/webp'}:raise ValueError('unsupported image')
    if path.stat().st_size>3_000_000:raise ValueError('image exceeds 3MB cap')
    return {'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode('ascii')}}



def explicit_native_tool_roundtrip(client, model, real_tender):
    """A tool call must be native, its Python handler executed, and model resumed."""
    messages=[{'role':'system','content':'Call inspect_tender as a native function before giving a JSON answer.'},
              {'role':'user','content':'Use inspect_tender to retrieve the official tender metadata, then summarize it.'}]
    try:
        first=client.complete(model,messages,tools=[TOOL_SPECS[0]],max_tokens=420)
        native=first.message.get('tool_calls') or []
        out={'status':'api_success','native_tool_calls':len(native),
             'model_returned':first.model,'first_usage':first.usage,'first_elapsed_seconds':first.elapsed_seconds,
             'first_cost_reported_usd':first.cost_usd,'python_tools_executed':0,
             'continuation_received':False}
        if not native:
            out['status']='api_success_but_no_native_tool_call'
            return out
        messages.append({'role':'assistant','content':first.message.get('content') or '', 'tool_calls':native})
        for call in native[:2]:
            function=call.get('function') or {}
            if function.get('name') != 'inspect_tender':
                messages.append({'role':'tool','tool_call_id':call['id'],'content':'{"status":"blocked"}'})
                continue
            result=TenderTools(real_tender).inspect_tender()
            out['python_tools_executed']+=1
            messages.append({'role':'tool','tool_call_id':call['id'],
                             'content':json.dumps({'status':result.status,'facts':result.facts,
                                                   'evidence':result.evidence},ensure_ascii=False)})
        second=client.complete(model,messages,max_tokens=450)
        out.update(continuation_received=True,second_usage=second.usage,
                   second_elapsed_seconds=second.elapsed_seconds,
                   second_cost_reported_usd=second.cost_usd,
                   response_excerpt=(second.message.get('content') or '')[:350])
        return out
    except (MissingKey,ModelUnavailable,ProviderError) as exc:
        return {'status':'blocked','error_type':type(exc).__name__,'error':str(exc)}


def live_official_text(url, max_bytes=250000):
    """One read-only public official page, with strict domain check and byte limit."""
    import urllib.request
    from urllib.parse import urlsplit
    allowed={'www.argentina.gob.ar','pncp.gov.br'}
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname not in allowed:
        return {'status':'blocked_unapproved_url'}
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'public_research-TenderScout-Research/1.0'}),timeout=18) as resp:
            if urlsplit(resp.geturl()).hostname not in allowed:raise ValueError('Disallowed redirect')
            data=resp.read(max_bytes+1)
        if len(data)>max_bytes:return {'status':'too_large'}
        # PNCP is an official JSON API, not HTML. Preserve its field names
        # and prevent accidental markup reparsing of API evidence.
        if data.lstrip().startswith((b'{',b'[')):
            obj=json.loads(data)
            txt=json.dumps(obj,ensure_ascii=False)[:16000]
        else:
            from lxml import html
            source=html.fromstring(data)
            for bad in source.xpath('//script|//style'):bad.drop_tree()
            main=(source.xpath('//main') or [source])[0]
            txt=' '.join(main.text_content().split())[:16000]
        return {'status':'fetched','source_url':url,'text':txt}
    except Exception as exc:return {'status':'unavailable','error_type':type(exc).__name__}

def run_benchmark(output:Path,mea_image:Path|None,pdf_file:Path|None):
    c=OpenRouterClient(timeout=50)
    report={'type':'openrouter_real_api_benchmark','timestamp_utc':None,'key_present':bool(os.environ.get('OPENROUTER_API_KEY')),
            'models':{},'sources':{},'controls_labeled_synthetic':True}
    from datetime import datetime,timezone
    report['timestamp_utc']=datetime.now(timezone.utc).isoformat()
    try:
        listings=c.catalog(refresh=True)
        report['catalog_status']='ok'; report['catalog_entries']=len(listings)
    except ProviderError as e:
        report['catalog_status']='unavailable';report['catalog_error']=str(e)
        listings=[]
    for model in FREE_MODELS:
        modelreport={}
        if not listings:
            modelreport['preflight']={'status':'blocked_catalog_unavailable'}
        else:
            try:
                modelreport['preflight']=c.check(model)
                checks={}
                for capability, props in [('tool_calling',{'tools':True}),('image_input',{'image':True})]:
                    try:c.check(model,**props);checks[capability]='advertised'
                    except ModelUnavailable as exc:checks[capability]='not_advertised'
                modelreport['capability_checks']=checks
            except ModelUnavailable as exc:modelreport['preflight']={'status':'unsupported','reason':str(exc)}
        if not report['key_present']:
            for test in 'ABCDE':modelreport['test_'+test]={'status':'not_run_missing_key'}
            report['models'][model]=modelreport
            continue
        if modelreport.get('preflight',{}).get('status') == 'unsupported' or not listings:
            for test in 'ABCDE':modelreport['test_'+test]={'status':'blocked_model_preflight'}
            report['models'][model]=modelreport
            continue
        # A: actual native tool call -> Python ToolResult -> model continuation.
        try:
            from integration.collector_to_tender import to_tender
            handoff=json.loads((ROOT/'tests/fixtures/collector_run_dayone_handoff.json').read_text(encoding='utf-8'))
            real_dayone=to_tender(handoff,handoff['tenders'][0])
            modelreport['test_A']=explicit_native_tool_roundtrip(c,model,real_dayone) if modelreport['capability_checks']['tool_calling']=='advertised' else {'status':'not_run_tool_calling_not_advertised'}
        except Exception as exc:
            modelreport['test_A']={'status':'error','error_type':type(exc).__name__}
        # B: only official MEA image, never fabricate a screenshot or substitute a synthetic control.
        if mea_image and mea_image.is_file() and modelreport['capability_checks']['image_input']=='advertised':
            modelreport['test_B']=_obtain_response(c,model,[
                {'role':'system','content':SYSTEM},
                {'role':'user','content':[{'type':'text','text':'This is the official MEA tender Email image. Transcribe the exact address only if every character is visible, with uncertainty in JSON.'},_img(mea_image)]}],image=True)
        else:modelreport['test_B']={'status':'not_run_missing_official_mea_image_or_vision_not_advertised'}
        # C: send original text and a real rasterized PDF page separately.
        if pdf_file and pdf_file.is_file():
            from collectors.common.document_text import extract
            info=extract(pdf_file,max_pages=8,max_text_chars=35000)
            source_text='\n'.join(x.get('text','') for x in info['parts'])[:25000]
            modelreport['test_C_text']=_obtain_response(c,model,[
                {'role':'system','content':SYSTEM},
                {'role':'user','content':'Extract Indonesian tender bid deadline, time and contact with original excerpts. Do not confuse registration deadline.\n'+source_text}])
            try:
                if modelreport['capability_checks']['image_input']!='advertised':
                    raise ModelUnavailable('Vision not advertised')
                import fitz
                rendered=[]
                content=[{'type':'text','text':'Read these ORIGINAL Indonesian official PDF pages rendered as pixels. Extract name, email, bid submission deadline and exact original excerpts, with page number. Do not mistake registration cutoff for bid submission. If a fact is absent use null.'}]
                with fitz.open(pdf_file) as d:
                    for i in range(min(len(d),2)):
                        pix=d[i].get_pixmap(matrix=fitz.Matrix(1.2,1.2),alpha=False)
                        path=output.parent/f'__temporary_pdf_page_{i+1}.png'
                        pix.save(str(path));rendered.append(path)
                        content.append({'type':'text','text':f'Original page {i+1}'})
                        content.append(_img(path))
                modelreport['test_C_scanned']=_obtain_response(c,model,[
                    {'role':'system','content':SYSTEM},
                    {'role':'user','content':content}],image=True)
                for path in rendered:path.unlink(missing_ok=True)
            except Exception as exc:modelreport['test_C_scanned']={'status':'blocked_pdf_render','error_type':type(exc).__name__}
            modelreport['test_C_expected_submission_date']='2026-10-06'
            modelreport['test_C_expected_submission_time']='13:30'
            modelreport['test_C_expected_timezone']='Asia/Jakarta'
            for kind in ('test_C_text','test_C_scanned'):
                a=modelreport.get(kind,{}).get('answer') or {}
                modelreport[kind]['deadline_date_exact_match']=(a.get('deadline_date')=='2026-10-06') if a else None
                modelreport[kind]['deadline_time_exact_match']=(a.get('deadline_time')=='13:30') if a else None
                modelreport[kind]['timezone_reported'] = a.get('timezone') if a else None
        else:
            modelreport['test_C_text']={'status':'not_run_missing_official_pdf'}
            modelreport['test_C_scanned']={'status':'not_run_missing_official_pdf'}
        modelreport['test_D']={}
        for example in MULTILINGUAL:
            res=_obtain_response(c,model,[
                {'role':'system','content':SYSTEM},
                {'role':'user','content':'Extract field values only from this explicitly synthetic language control:\n'+example['text']}],max_tokens=450)
            answer=res.get('answer') or {}
            # Explicit score only for an exact ISO deadline; no judgement from narrative.
            res['deadline_exact_match']=(answer.get('deadline_date')==example['date']) if answer else None
            modelreport['test_D'][example['language']]=res
        # Real official AMBA I and PNCP pages, kept separate from synthetic controls.
        modelreport['real_source_text_checks']={}
        for source_name, url in [
            ('AMBA_I','https://www.argentina.gob.ar/economia/energia/licitacion-amba-i'),
            ('PNCP_public_API','https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao?dataInicial=20261120&dataFinal=20261126&pagina=1&tamanhoPagina=10&codigoModalidadeContratacao=4')]:
            page=live_official_text(url)
            report['sources'][source_name]={'url':url,'status':page['status']}
            if page.get('status')=='fetched' and page.get('text'):
                modelreport['real_source_text_checks'][source_name]=_obtain_response(c,model,[
                    {'role':'system','content':SYSTEM},
                    {'role':'user','content':'This is text from the current official source page '+url+'\nExtract tender details and exact evidence, or null for absent values. Do NOT infer current open status.\n'+page['text']}],max_tokens=600)
            else:modelreport['real_source_text_checks'][source_name]={'status':'not_run_source_unavailable'}
        # E: actual tool-use loop on a real archival Dayone candidate.
        try:
            from integration.collector_to_tender import to_tender
            fixture=json.loads((ROOT/'tests/fixtures/collector_run_dayone_handoff.json').read_text(encoding='utf-8'))
            obj=to_tender(fixture,fixture['tenders'][0])
            pdf_docs={d['url']:str(pdf_file) for d in obj.get('documents',[]) if d.get('url') and pdf_file and pdf_file.is_file()}
            limits=AgentLimits(max_actions=5,max_tokens=7000,max_seconds=120,max_documents=2,max_completions=7)
            modelreport['test_E']=BoundedOrchestrator(c,limits=limits).run(obj,model,documents=pdf_docs,
                           root=pdf_file.parent if pdf_file else None,
                           checkpoint=output.parent/('checkpoint_'+model.replace('/','_').replace(':','_')+'.json'))
        except Exception as exc:modelreport['test_E']={'status':'error','error_type':type(exc).__name__,'message':str(exc)[:180]}
        report['models'][model]=modelreport
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--mea-image',type=Path)
    p.add_argument('--pdf',type=Path)
    args=p.parse_args()
    result=run_benchmark(args.output,args.mea_image,args.pdf)
    print(json.dumps({'models':{k:{'preflight':v.get('preflight'),
            'tests':{a:v.get('test_'+a,{}).get('status','structured_result') for a in 'ABCE'}}
            for k,v in result['models'].items()},'key_present':result['key_present'],
            'catalog_status':result['catalog_status']},ensure_ascii=False,indent=2))

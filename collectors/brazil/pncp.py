"""PNCP adapter extending the recovered pilot's URL/identity helpers."""
from urllib.parse import urlencode,urlsplit,parse_qs
from collectors.common.pagination import Page
from collectors.common.runtime import SourceError
from collectors.common.records import notice
from .pncp_legacy import purchase_path,detail_url,record_url,attachments_url

API='https://pncp.gov.br/api/consulta/v1/contratacoes/'
def listing(options,modality):
    mode=options.get('mode','full');route='proposta' if options.get('open_proposals') else 'atualizacao' if mode=='incremental' else 'publicacao'
    params=dict(dataFinal=options['end'].replace('-',''),codigoModalidadeContratacao=modality,pagina=1,tamanhoPagina=options.get('page_size',50))
    if route!='proposta':params['dataInicial']=options['start'].replace('-','')
    return API+route+'?'+urlencode(params)
def page(obj,url):
    if obj is not None and not isinstance(obj,dict):raise SourceError("Expected JSON object envelope","parse_error")
    if obj is None:return Page([],None,0)
    rows=obj.get('data');total=obj.get('totalRegistros');pages=obj.get('totalPaginas');p=obj.get('numeroPagina');requested=int(parse_qs(urlsplit(url).query)['pagina'][0])
    if not isinstance(rows,list) or any(type(x) is not int for x in [total,pages,p]) or min(total,pages,p)<0 or p!=requested:raise SourceError('Invalid PNCP pagination envelope','parse_error')
    nxt=None
    if p<pages:
        q={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()};q['pagina']=p+1;nxt=url.split('?')[0]+'?'+urlencode(q)
    return Page(rows,nxt,total)
def parse(config,row,observed):
    try:url=detail_url(row)
    except ValueError:raise SourceError('PNCP official URL identity invalid','parse_error')
    r=notice(config,id=row['numeroControlePNCP'],title=row.get('objetoCompra'),description=row.get('informacaoComplementar'),buyer=(row.get('orgaoEntidade') or {}).get('razaoSocial'),url=url,raw=row,published=row.get('dataPublicacaoPncp'),status=row.get('situacaoCompraNome'),deadline=row.get('dataEncerramentoProposta'),now=observed,locator='PNCP '+row['numeroControlePNCP'])
    r['raw_payload']['portal_url']=record_url(row)
    r['raw_payload']['canonical_identity']={'namespace':'pncp','value':row['numeroControlePNCP']}
    # Unzoned Brazilian timestamps are retained verbatim, never assumed UTC.
    return r
def documents(fetch,record):
    row=record['raw_payload']['source_record'];url=attachments_url(row,'arquivos');obj=fetch.json(url)
    if not isinstance(obj,list):raise SourceError('PNCP attachment response is not array','parse_error')
    result=[]
    for d in obj:
        link=d.get('url')
        if not link and d.get('sequencialDocumento'):link=attachments_url(row,'arquivos/'+str(d['sequencialDocumento']))
        if link:result.append(dict(title=d.get('titulo') or 'Official attachment',url=link,kind='other',access='unknown'))
    return result

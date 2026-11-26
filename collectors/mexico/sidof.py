"""Documented SEGOB gazette API: real daily editions, not the static 2016 JSON example."""
import re
from datetime import date,datetime,timedelta
from lxml import html
from collectors.common.pagination import Page
from collectors.common.records import notice,text,EMAIL,evidenced_contacts
from collectors.common.runtime import SourceError,utc
API='https://sidof.segob.gob.mx/dof/sidof/notas'
def partitions(options):
    day=date.fromisoformat(options['start']);end=date.fromisoformat(options['end'])
    while day<=end:yield day.isoformat();day+=timedelta(days=1)
def listing(options,partition):return API+'/'+date.fromisoformat(partition).strftime('%d-%m-%Y')
def page(obj,url):
    if not isinstance(obj,dict) or obj.get('messageCode')!=200:raise SourceError('SIDOF daily service failed/invalid envelope','parse_error')
    rows=[];day=url.rsplit('/',1)[-1]
    for key in ['NotasMatutinas','NotasVespertinas','NotasExtraordinarias']:
        if not isinstance(obj.get(key),list):raise SourceError('SIDOF response omitted an edition; completeness unconfirmed','parse_error')
        for r in obj[key]:
            if not isinstance(r,dict) or not r.get('codNota') or r.get('fecha')!=day:raise SourceError('SIDOF date/identity mismatch, possibly static sample rather than requested date','parse_error')
            r['_source_key']=str(r['codNota']);r['_edition']=key;rows.append(r)
    # The official service documents all editions for a day in this response, without an offset.
    return Page(rows,None,len(rows))
def must_inspect(row):
    return bool(row.get('titulo') and re.search(r'COMISI[OÓ]N FEDERAL DE ELECTRICIDAD|CFE\b',str(row.get('codOrgaDos',''))+' '+str(row.get('titulo','')),re.I))
def parse(config,row,observed):
    return notice(config,id=row['codNota'],title=row.get('titulo'),buyer=row.get('codOrgaDos'),url='https://sidof.segob.gob.mx/notas/'+str(row['codNota']),raw=row,published=datetime.strptime(row['fecha'],'%d-%m-%Y').date().isoformat(),status='Official gazette publication; procurement portal status unconfirmed',now=observed,locator='SIDOF documented daily edition API')
def enrich(fetch,r):
    ok,why=fetch.public_file_allowed(r['tender_url'])
    if not ok:raise SourceError(why,'auth_required')
    url=API+'/nota/'+r['source_tender_id'];obj=fetch.json(url)
    detail=(obj.get('Nota') or obj.get('nota')) if isinstance(obj,dict) else None
    if not isinstance(obj,dict) or obj.get('messageCode')!=200 or not isinstance(detail,dict) or str(detail.get('codNota'))!=r['source_tender_id']:raise SourceError('SIDOF note identity not confirmed','parse_error')
    return apply_detail(r,detail,url,utc())

def apply_detail(r,detail,url,observed):
    content=detail.get('cadenaContenido') or detail.get('contenido') or ''
    if not isinstance(content,str) or not content.strip():raise SourceError('SIDOF note has no public body','parse_error')
    h=html.fromstring(content);body=text(h.text_content());r['description_original']=body
    r['buyer_original']=detail.get('nombOrganismo') or r['buyer_original']
    raw=r['raw_payload'];raw.update(verified_detail=detail,detail_url=url,detail_checked_at=observed)
    raw['discovered_contest_ids']=sorted(set(re.findall(r'CFE-[A-Z0-9-]{10,}',body,re.I)))
    issue='Gazette publication is not proof of unchanged live procurement portal status; multiple contest dates are not merged into one deadline.'
    if issue not in raw['missing_fields']:raw['missing_fields'].append(issue)
    raw.setdefault('original_publication_title',r['title_original'])
    project=re.search(r'para la contrataci[oó]n de los trabajos de la\s+(.+?)\.\s+Los interesados',body,re.I) or re.search(r'Descripci[oó]n del Concurso:\s*(.+?)\s+Fecha de publicaci[oó]n',body,re.I)
    if project and len(raw['discovered_contest_ids'])==1:r['title_original']=project[1].strip()
    if re.search(r'AVISO (?:MULTIPLE )?DE ADJUDICACI[OÓ]N DE CONTRATOS',body[:500],re.I):r['raw_status']='Official contract award notice'
    if len(raw['discovered_contest_ids'])==1:
        cutoff=re.search(r'L[ií]mite para presentaci[oó]n de ofertas:\s*(\d{1,2}/\d{1,2}/\d{4}),?\s*(\d{1,2}:\d{2})',body,re.I)
        epc=re.search(r'Presentaci[oó]n de Ofertas y Apertura de Ofertas T[eé]cnicas:\s*(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})\s+(\d{1,2}:\d{2})',body,re.I)
        if cutoff:r['deadline_text']=datetime.strptime(cutoff[1]+' '+cutoff[2],'%d/%m/%Y %H:%M').isoformat();raw['deadline_label_original']=cutoff[0]
        elif epc:
            months=['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
            if epc[2].lower() in months:
                day=f'{epc[3]}-{months.index(epc[2].lower())+1:02d}-{int(epc[1]):02d}';r['deadline_text']=day+'T'+epc[4]+':00';raw['deadline_label_original']=epc[0]
        if cutoff or epc:raw['deadline_basis']='Gazette explicitly labelled offer presentation event; current portal status still unconfirmed, source timezone unresolved'
    # Preserve publication-level official procurement inquiry channels. Specific contest attribution
    # and deadlines remain unconfirmed when an announcement contains several processes.
    contacts=[];person=re.search(r'El personal de contacto es\s+(.+?),\s+con (?:correo|domicilio)',body,re.I)
    raw['contact_person_group_original']=person[1] if person else None
    for match in EMAIL.finditer(body):
        context=body[max(0,match.start()-250):match.end()+120]
        if re.search(r'correo|electr[oó]nico|informes|informaci[oó]n|contacto|consultas',context,re.I) and not any(c['email']==match[0] for c in contacts):contacts.append(dict(email=match[0],name=person[1] if person else None,department='Official gazette procurement publication contact',context=context))
    cs,ev=evidenced_contacts(contacts,r['tender_url'],'Official procurement publication inquiry text; publication-level contact',observed);raw['contacts']=cs;raw['evidence']=list({e['evidence_id']:e for e in raw['evidence']+ev}.values())
    r['contact_hints']=[dict(name=c.get('name'),email=c.get('email'),phone=c.get('phone'),source_url=r['tender_url']) for c in cs]
    raw['contact_scope']='publication level; exact contest attribution requires review'
    return r

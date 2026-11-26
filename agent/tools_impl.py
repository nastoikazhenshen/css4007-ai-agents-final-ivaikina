"""Safe AI tool implementations for official tenders AND future-project proposals.

The seven tool surfaces are read-only; return evidenced hypotheses, never mutate
TenderRecord or decide Active. They reuse common document_text and SourceConfig.
"""
from __future__ import annotations
import base64
import hashlib
import io
import ipaddress
import json
import mimetypes
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urljoin

from .tool_contract import ToolResult
from .official_research import (
    SourcePolicy, OfficialReader, ResearchBlocked, visible_text, contact_evidence,
    parse_dates_with_evidence, official_anchors, STATUS_TERMINAL,
)


def _valid_https(url):
    try:
        p=urlsplit(url)
        if not isinstance(url,str) or p.scheme!='https' or p.username or p.password or not p.hostname or p.port not in (None,443):return False
        try:ipaddress.ip_address(p.hostname);return False
        except ValueError:return '.' in p.hostname and not p.hostname.endswith('.local')
    except (TypeError,ValueError):return False


class TenderTools:
    def __init__(self, tender:dict, *, document_files=None, image_files=None,
                 document_root=None, reader:OfficialReader|None=None,
                 enable_network:bool=False, registry_root=None):
        self.tender=tender
        self.docs=document_files or {}
        self.images=image_files or {}
        self.root=Path(document_root).resolve() if document_root else None
        self._scratch=tempfile.TemporaryDirectory(prefix='public_research_evidence_')
        self._runtime_images={}
        self._extracted={}
        self.policy=SourcePolicy.load((tender.get('source') or {}).get('source_id',''),
                                      root=Path(registry_root) if registry_root else __import__('agent.official_research',fromlist=['ROOT']).ROOT)
        self.reader=reader if enable_network else None
        if enable_network and reader is None:self.reader=OfficialReader(self.policy)
        self._linked_contact_urls=[]
        self._linked_docs=[]
        self._linked_images=[]

    def close(self):
        self._scratch.cleanup()

    def _allowed_path(self,value):
        if self.root is None:raise PermissionError('Document root is not configured')
        p=Path(value).resolve()
        if not p.is_relative_to(self.root):raise PermissionError('File path escapes permitted directory')
        if not p.is_file():raise FileNotFoundError('Document not available')
        if p.stat().st_size>20_000_000:raise ValueError('File exceeds byte cap')
        return p

    def _evidence(self,field=None):
        return [x for x in (self.tender.get('evidence') or []) if isinstance(x,dict)
                and _valid_https(x.get('source_url')) and
                (field is None or str(x.get('field_path') or '').startswith(field))][:20]

    def _direct_url(self):
        source=self.tender.get('source') or {}
        return source.get('tender_url') or self.tender.get('official_plan_url')

    def _source_url(self):
        return (self.tender.get('source') or {}).get('source_url') or self.tender.get('official_plan_url')

    def _candidate_urls(self):
        urls=[self._direct_url()]
        urls.extend(x.get('url') for x in self.tender.get('documents') or [])
        return [u for u in urls if _valid_https(u)]

    def _fetch(self,url,*,allow_extra=False):
        if not self.reader:return None,'network_not_enabled'
        if not _valid_https(url):return None,'invalid_url'
        if not (url in self._candidate_urls() or (allow_extra and url in (self._linked_contact_urls+self._linked_docs+self._linked_images))):
            return None,'not_linked_to_candidate'
        try:
            o=self.reader.get(url)
            return o,None
        except ResearchBlocked as exc:return None,str(exc)

    def inspect_tender(self):
        planned=self.tender.get('record_type')=='planned_project_proposal'
        p=self.tender.get('project') or {}
        s=self.tender.get('source') or {}
        return ToolResult('success',self._evidence() or list(self.tender.get('evidence') or [])[:10],{
            'record_type':'planned_project_proposal' if planned else 'tender',
            'title_original':self.tender.get('title_original') if planned else p.get('title'),
            'country':self.tender.get('country_code') if planned else p.get('country_code'),
            'buyer':self.tender.get('company') if planned else p.get('buyer_name'),
            'official_url':self._direct_url(),
            'description':(p.get('description') or '')[:5500],
            'documents':[{'title':d.get('title'),'url':d.get('url'),'kind':d.get('kind')}
                         for d in (self.tender.get('documents') or [])][:30],
            'missing_fields':(self.tender.get('assessment') or {}).get('missing_fields',[]),
            'central_route':(self.tender.get('assessment') or {}).get('decision') if not planned else 'planned_projects',
            'contact_missing_does_not_drop_candidate':True,
            'planned_project_not_active_tender':planned,
        })

    def _read_bytes(self,path,url):
        from collectors.common.document_text import extract
        info=extract(path,max_pages=12,max_text_chars=30_000,max_total_bytes=20_000_000)
        excerpts=[];evidence=[];needed=[]
        for i,x in enumerate((info.get('parts') or [])[:8]):
            snippet=(x.get('text') or '')[:16_000]
            excerpts.append({'text':snippet,'locator':x.get('locator'),
                             'sha256':x.get('sha256'),'needs_ocr':x.get('needs_ocr'),
                             'format':x.get('format'),'pages':x.get('pages')})
            evidence.append({'source_url':url,'location':x.get('locator'),
                             'sha256':x.get('sha256'),'excerpt':snippet[:600]})
            if x.get('needs_ocr') and x.get('format')=='pdf':needed.append(i)
        image_ids=[]
        if needed:
            try:
                import fitz
                with fitz.open(path) as pdf:
                    for page_no in range(min(len(pdf),2)):
                        pix=pdf[page_no].get_pixmap(matrix=fitz.Matrix(1.4,1.4),alpha=False)
                        data=pix.tobytes('png')
                        if len(data)>3_000_000:continue
                        key='pdf_page_'+hashlib.sha256((url+str(page_no)+hashlib.sha256(data).hexdigest()).encode()).hexdigest()[:18]
                        f=Path(self._scratch.name)/(key+'.png');f.write_bytes(data)
                        self._runtime_images[key]=f;image_ids.append(key)
            except (OSError,ValueError,RuntimeError):pass
        issues=info.get('issues') or []
        return ToolResult('success' if excerpts and not issues else 'partial',evidence,{
            'url':url,'excerpts':excerpts,'issues':issues,
            'image_ids_for_vision':image_ids,'text_extraction_complete':not issues,
            'deadline_candidates':[x for e in excerpts for x in parse_dates_with_evidence(e['text'],url)][:10],
            'contact_leads':[x for e in excerpts for x in contact_evidence(e['text'],url,notice_specific=True)][:15],
            'no_auto_status_promotion':True,
        })

    def inspect_document(self,document_url):
        if not _valid_https(document_url) or document_url not in ({d.get('url') for d in self.tender.get('documents') or []}|set(self._linked_docs)):
            return ToolResult('blocked',[],{},'document_url_not_in_tender')
        if document_url in self.docs:
            try:result=self._read_bytes(self._allowed_path(self.docs[document_url]),document_url)
            except (OSError,ValueError,PermissionError) as exc:
                return ToolResult('blocked',[],{},type(exc).__name__)
            return result
        fetch,err=self._fetch(document_url,allow_extra=True)
        if err:return ToolResult('partial',[],{'url':document_url,'reason':err},'document_not_downloaded')
        if fetch['content_type'] not in {'application/pdf','application/octet-stream'} or not fetch['content'].startswith(b'%PDF'):
            return ToolResult('partial',[],{'url':document_url,'reason':'unsupported_or_not_pdf'},'unsupported_document')
        f=Path(self._scratch.name)/('official_'+fetch['sha256']+'.pdf');f.write_bytes(fetch['content'])
        result=self._read_bytes(f,document_url)
        result.facts['network_sha256']=fetch['sha256']
        return result

    def extract_image_text(self,image_id):
        """Return pixels to next LLM message; *not* an assertion of successful OCR."""
        if image_id not in self.images and image_id not in self._runtime_images:
            return ToolResult('blocked',[],{},'image_not_allowlisted'),None
        try:
            p=self._runtime_images.get(image_id)
            if p is None:p=self._allowed_path(self.images[image_id])
            data=p.read_bytes()
            if len(data)>3_000_000:raise ValueError('image too large')
            mime=mimetypes.guess_type(p.name)[0]
            if mime not in ('image/png','image/jpeg','image/webp'):
                return ToolResult('blocked',[],{},'unsupported_image_mime'),None
            part={'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(data).decode()}}
            return ToolResult('success',[{'location':'approved_image:'+image_id,'sha256':hashlib.sha256(data).hexdigest()}],
                              {'image_id':image_id,'image_supplied_to_model':True,
                               'ocr_status':'pending_model_response_verification'}),part
        except (OSError,ValueError,PermissionError) as exc:
            return ToolResult('blocked',[],{},type(exc).__name__),None

    def translate_and_extract(self,document_url):
        res=self.inspect_document(document_url)
        if res.status not in ('success','partial') or not res.facts.get('excerpts'):return res
        res.facts['translation_status']='original_text_supplied_to_model_translation_required'
        res.facts['original_excerpts_preserved']=True
        res.facts['fields_without_evidence_stay_null']=True
        return res

    def verify_status_and_deadline(self):
        status=self.tender.get('procurement') or {}
        assessment=self.tender.get('assessment') or {}
        proposals=[];status_signals=[];fetch_status='not_requested';source_checks=[]
        url=self._direct_url()
        if url and self.reader:
            obj,err=self._fetch(url)
            fetch_status=err or 'fetched'
            if obj:
                txt=visible_text(obj['content'],obj['content_type'])
                proposals.extend(parse_dates_with_evidence(txt,url))
                source_checks.append({'url':url,'status':'fetched','sha256':obj['sha256']})
                if STATUS_TERMINAL.search(txt):
                    m=STATUS_TERMINAL.search(txt)
                    status_signals.append({'status_candidate':m.group(),
                         'source_url':url,'original_excerpt':txt[max(0,m.start()-70):m.end()+65],
                         'unconfirmed_for_this_specific_tender':True})
        # When no confirmed submission deadline exists, examine linked documents.
        # Do not use an application/registration cutoff as a bid deadline.
        if (not status.get('deadline_date') or not self._evidence('procurement.deadline')):
            for doc in (self.tender.get('documents') or [])[:2]:
                if not doc.get('url'):continue
                result=self.inspect_document(doc['url'])
                proposals.extend(result.facts.get('deadline_candidates') or [])
                source_checks.append({'url':doc['url'],'status':result.status,
                        'error':result.error_code})
        unique=[];seen=set()
        for x in proposals:
            key=(x.get('source_url'),x.get('deadline_date_proposed'),x.get('deadline_time_proposed'))
            if key not in seen:seen.add(key);unique.append(x)
        return ToolResult('success' if (unique or self._evidence('procurement.')) else 'partial',
            self._evidence('procurement.')+unique[:8],{
                'published':status.get('published_on'),'deadline_date':status.get('deadline_date'),
                'deadline_time':status.get('deadline_time'),'deadline_timezone':status.get('deadline_timezone'),
                'official_status':status.get('status'),'central_routing':assessment.get('decision'),
                'deadline_proposals':unique[:10],'terminal_status_signals':status_signals,
                'network_result':fetch_status,'source_checks':source_checks,
                'time_of_registration_is_not_bid_deadline':True,
                'no_auto_active_promotion':True,
            })

    def _discover_notice_media(self,blob,base_url):
        from lxml import html
        try:doc=html.fromstring(blob)
        except (ValueError,TypeError):return [],[]
        documents=[];images=[]
        for a in doc.xpath('//a[@href]'):
            link=urljoin(base_url,a.get('href'))
            if (re.search(r'\.pdf(?:$|\?)',urlsplit(link).path,re.I) and
                    self.policy.permitted(link) and link not in documents):documents.append(link)
        for img in doc.xpath('//img[@src]'):
            parent=img.getparent()
            context=' '.join([img.get('alt') or '',img.get('title') or '',
                parent.text_content()[:120] if parent is not None else ''])
            if re.search(r'e-?mail|correio|อีเมล|อีเมล์|почт',context,re.I):
                link=urljoin(base_url,img.get('src'))
                if self.policy.permitted(link) and link not in images:images.append(link)
        return documents[:3],images[:2]

    def search_official_contact(self):
        existing=[]
        for c in self.tender.get('contacts') or []:
            existing.append({'name':c.get('name'),'email':c.get('email'),'phone':c.get('phone'),
                   'role':c.get('role'),'verified':c.get('verified',False),
                   'evidence_url':c.get('evidence_url')})
        url=self._direct_url();leads=[];errors=[];searched=[];image_ids=[]
        if url and self.reader:
            page,err=self._fetch(url)
            if err:errors.append({'url':url,'reason':err})
            if page:
                searched.append(url)
                body=visible_text(page['content'],page['content_type'])
                leads.extend(contact_evidence(body,url,notice_specific=True))
                if page['content_type']=='text/html':
                    self._linked_contact_urls=official_anchors(page['content'],url,self.policy)
                    self._linked_docs,self._linked_images=self._discover_notice_media(page['content'],url)
                    # Prioritize linked procurement-contact pages, but do not
                    # follow arbitrary links or a second hop.
                    for link in self._linked_contact_urls[:1]:
                        follow,followerr=self._fetch(link,allow_extra=True)
                        if followerr:errors.append({'url':link,'reason':followerr});continue
                        searched.append(link)
                        leads.extend(contact_evidence(visible_text(follow['content'],follow['content_type']),link,notice_specific=False))
                    # OCR is a separate model step; fetch only exact linked image.
                    for img_url in self._linked_images[:1]:
                        loaded,imgerr=self._fetch(img_url,allow_extra=True)
                        if imgerr:errors.append({'url':img_url,'reason':imgerr});continue
                        mime=loaded['content_type']
                        if mime not in ('image/png','image/jpeg','image/webp'):continue
                        ext={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}[mime]
                        key='official_contact_'+loaded['sha256'][:18]
                        path=Path(self._scratch.name)/(key+ext)
                        if len(loaded['content'])<=3_000_000:
                            path.write_bytes(loaded['content'])
                            self._runtime_images[key]=path;image_ids.append(key)
        # First two already captured or directly linked tender PDFs also matter;
        # parse them read-only and preserve contacts even when email is absent.
        pdf_urls=[x.get('url') for x in (self.tender.get('documents') or []) if x.get('url')]
        pdf_urls += self._linked_docs
        for link in list(dict.fromkeys(pdf_urls))[:2]:
            if self.reader and self.reader.requests>=self.reader.max_requests:break
            info=self.inspect_document(link)
            leads.extend(info.facts.get('contact_leads') or [])
            searched.append(link)
        unique=[];seen=set()
        for c in leads:
            key=(str(c.get('email')).lower(),c.get('evidence_url'))
            if key in seen:continue
            seen.add(key);unique.append(c)
        return ToolResult('success' if existing or unique or image_ids else 'partial',
            self._evidence('contacts.')+
            [{'source_url':x['evidence_url'],'location':'official text email context',
              'excerpt':x['original_excerpt']} for x in unique if x.get('evidence_url')][:20],
            {'contacts_existing':existing,'new_contact_candidates':unique[:20],
             'official_pages_checked':searched,'network_issues':errors,
             'image_ids_for_vision':image_ids,
             'no_contact_is_not_rejection':True,
             'new_leads_require_attribution_before_contact_verification':True})

    def assess_commercial_relevance(self):
        from integration.commercial import initial_assessment
        planned=self.tender.get('record_type')=='planned_project_proposal'
        p=self.tender.get('project') or {}
        title=self.tender.get('title_original') if planned else p.get('title')
        description=self.tender.get('description_original') if planned else p.get('description')
        cats,relevance,demand,line=initial_assessment(title,description)
        # Broader multilingual collector-screening rules may add relevant
        # feeder/insulator/fitting signals, but are NEVER final qualification.
        try:
            from collectors.common.relevance import assess as screening_assess
            screening=screening_assess(title,description)
        except ImportError:
            screening={'verdict':'not_checked','reason':'collector_rules_unavailable'}
        # Existing collector screening historically excluded underground-only.
        # New business scope expressly retains UGC/SKTT as potential, even if
        # the power-line product BOM has not yet been verified.
        if 'underground_cable' in cats and screening['verdict']=='excluded':
            screening={**screening,'verdict':'needs_documents',
                'reason':'UGC retained by expanded public_research commercial scope; component demand unconfirmed'}
        if not cats and screening['verdict'] in ('relevant','needs_documents'):
            relevance='potential'
        return ToolResult('success',self._evidence('project.') or list(self.tender.get('evidence') or [])[:10],{
            'categories':cats,'relevance':'potential' if planned and relevance in ('unknown','uncertain') else relevance,
            'screening_context':screening,
            'product_demand':demand,'line_flag':line,
            'project_stage':'planned_project' if planned else 'procurement',
            'retained_even_if_product_demand_unconfirmed':True,
            'caution':'Deterministic textual hypothesis; verify product specifications separately',
        })

    def execute(self,name,arguments):
        arguments=arguments if isinstance(arguments,dict) else {}
        if name=='inspect_tender':return self.inspect_tender(),None
        if name=='inspect_document':return self.inspect_document(arguments.get('document_url','')),None
        if name=='extract_image_text':return self.extract_image_text(arguments.get('image_id',''))
        if name=='translate_and_extract':return self.translate_and_extract(arguments.get('document_url','')),None
        if name=='verify_status_and_deadline':return self.verify_status_and_deadline(),None
        if name=='search_official_contact':return self.search_official_contact(),None
        if name=='assess_commercial_relevance':return self.assess_commercial_relevance(),None
        return ToolResult('blocked',[],{},'unknown_tool'),None


TOOL_SPECS=[
    {'type':'function','function':{'name':name,'description':description,
      'parameters':{'type':'object','additionalProperties':False,
                    'properties':props,'required':list(props)}}}
    for name,description,props in [
        ('inspect_tender','Read exact official tender/project identity and missing fields',{}),
        ('inspect_document','Read an already linked official PDF or saved document; identify scans',{'document_url':{'type':'string'}}),
        ('extract_image_text','Supply an approved image to vision model on the next turn, then validate OCR evidence',{'image_id':{'type':'string'}}),
        ('translate_and_extract','Supply original-language official document and locators for model extraction',{'document_url':{'type':'string'}}),
        ('verify_status_and_deadline','Read previously saved evidence and safely check the same official notice for date/status candidates',{}),
        ('search_official_contact','Research only the approved official tender page and directly linked procurement contact pages',{}),
        ('assess_commercial_relevance','Classify OHL, UGC, insulators, fittings, grid projects and planned projects without discarding unknowns',{}),
    ]]

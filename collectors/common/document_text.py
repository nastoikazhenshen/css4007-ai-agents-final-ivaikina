"""Bounded read-only PDF, DOCX and XLSX text, including members of public ZIPs."""
import hashlib,io,re,zipfile
from pathlib import Path,PurePosixPath
import fitz
from lxml import etree

def extract(path,*,max_members=100,max_member_bytes=32_000_000,max_total_bytes=128_000_000,max_pages=500,
            max_text_chars=2_000_000,max_rows=10_000,max_columns=256,max_cells=100_000):
    path=Path(path);parts=[];issues=[]
    def part(body,text,locator,kind,**metadata):
        if len(text)>max_text_chars:
            issues.append(dict(locator=locator,reason='text_budget'));text=text[:max_text_chars]
        parts.append(dict(text=text,sha256=hashlib.sha256(body).hexdigest(),locator=locator,
                          format=kind,pages=None,needs_ocr=False,**metadata))
    def safe_members(z,locator):
        members=[i for i in z.infolist() if not i.is_dir()]
        names=set();total=0
        if len(members)>max_members:
            issues.append(dict(locator=locator,reason='member_budget',members_available=len(members),members_read=0));return False
        for info in members:
            member=info.filename.replace('\\','/');p=PurePosixPath(member)
            if p.is_absolute() or '..' in p.parts or re.match(r'^[A-Za-z]:',member):
                issues.append(dict(locator=locator+' / '+member,reason='unsafe_member_path'));return False
            if member in names:
                issues.append(dict(locator=locator,reason='duplicate_archive_member'));return False
            names.add(member);total+=info.file_size
            if info.flag_bits&1:
                issues.append(dict(locator=locator+' / '+member,reason='encrypted_member'));return False
            if info.file_size>max_member_bytes or total>max_total_bytes or info.file_size>max(1,info.compress_size)*200:
                issues.append(dict(locator=locator+' / '+member,reason='archive_expansion_budget'));return False
        return True
    def office(body,locator):
        """Read OOXML without following external relationships or evaluating formulas/macros."""
        identified=False
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                names=set(z.namelist())
                kind='docx' if 'word/document.xml' in names else 'xlsx' if 'xl/workbook.xml' in names else None
                if kind is None:return False
                identified=True
                if not safe_members(z,locator):return True
                if any(n.lower().endswith('vbaproject.bin') for n in names):
                    issues.append(dict(locator=locator,reason='macro_enabled_document_unsupported'));return True
                # Reject DTDs/entities before handing any XML to an Office parser.
                for name in names:
                    if name.endswith(('.xml','.rels')):
                        data=z.read(name)
                        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                            issues.append(dict(locator=locator+' / '+name,reason='xml_dtd_or_entity_unsupported'));return True
                        parser=etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,huge_tree=False)
                        node=etree.fromstring(data,parser)
                        if node.getroottree().docinfo.doctype:
                            issues.append(dict(locator=locator+' / '+name,reason='xml_dtd_or_entity_unsupported'));return True
                if kind=='docx':
                    paragraphs=[]
                    selected=sorted(n for n in names if re.fullmatch(r'word/(?:document|footnotes|endnotes|header\d+|footer\d+)\.xml',n))
                    selected.sort(key=lambda n:n!='word/document.xml')
                    for name in selected:
                        parser=etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,huge_tree=False)
                        root=etree.fromstring(z.read(name),parser)
                        for paragraph in root.xpath('//*[local-name()="p"]'):
                            text=''.join(paragraph.xpath('.//*[local-name()="t"]/text()'))
                            if text:paragraphs.append(text)
                    content='\n'.join(paragraphs);part(body,content,locator+' / DOCX paragraphs and tables','docx')
                    if not content.strip():issues.append(dict(locator=locator,reason='no_text_in_office_document'))
                    return True
            from openpyxl import load_workbook
            book=load_workbook(io.BytesIO(body),read_only=True,data_only=False,keep_links=False)
            lines=[];cells=0;characters=0;formulas=0;stopped=False
            try:
                for sheet in book.worksheets:
                    lines.append('Worksheet: '+sheet.title)
                    if (sheet.max_row or 0)>max_rows or (sheet.max_column or 0)>max_columns:
                        issues.append(dict(locator=locator+' / '+sheet.title,reason='worksheet_dimension_budget'))
                    for row in sheet.iter_rows(max_row=min(sheet.max_row or max_rows,max_rows),max_col=min(sheet.max_column or max_columns,max_columns)):
                        for cell in row:
                            if cell.value is None:continue
                            value=str(cell.value)
                            if cell.data_type=='f':
                                formulas+=1;value='[Formula preserved; not evaluated] '+value
                            line=f'{cell.coordinate}: {value}';cells+=1;characters+=len(line)
                            if cells>max_cells or characters>max_text_chars:
                                issues.append(dict(locator=locator,reason='worksheet_cell_or_text_budget'));stopped=True;break
                            lines.append(line)
                        if stopped:break
                    if stopped:break
            finally:book.close()
            part(body,'\n'.join(lines),locator+' / XLSX worksheets and cell references','xlsx',
                 cells_read=min(cells,max_cells),formulas_preserved=formulas)
            if formulas:issues.append(dict(locator=locator,reason='formulas_not_evaluated',count=formulas))
            return True
        except (zipfile.BadZipFile,RuntimeError,ValueError,TypeError,IndexError,KeyError,etree.XMLSyntaxError,OSError,NotImplementedError) as e:
            if not identified:return False
            issues.append(dict(locator=locator,reason='invalid_or_unsupported_office_document',error=type(e).__name__));return True
    def pdf(body,locator):
        try:
            with fitz.open(stream=body,filetype='pdf') as d:
                pages=min(len(d),max_pages);content='\n'.join(d[i].get_text() for i in range(pages))
                if len(d)>pages:issues.append(dict(locator=locator,reason='page_budget',pages_available=len(d),pages_read=pages))
            if len(content)>max_text_chars:issues.append(dict(locator=locator,reason='text_budget'));content=content[:max_text_chars]
            parts.append(dict(text=content,sha256=hashlib.sha256(body).hexdigest(),locator=locator,format='pdf',pages=pages,needs_ocr=len(content.strip())<20))
            if len(content.strip())<20:issues.append(dict(locator=locator,reason='ocr_required'))
        except (ValueError,RuntimeError) as e:issues.append(dict(locator=locator,reason='invalid_pdf',error=type(e).__name__))
    with path.open('rb') as f:magic=f.read(8)
    if magic.startswith(b'%PDF'):
        if path.stat().st_size>max_total_bytes:issues.append(dict(locator='PDF',reason='byte_budget'))
        else:pdf(path.read_bytes(),'PDF')
    elif magic.startswith(b'PK'):
        if path.stat().st_size>max_total_bytes:
            return dict(parts=[],issues=[dict(locator=path.name,reason='byte_budget')],complete=False)
        if office(path.read_bytes(),path.name):return dict(parts=parts,issues=issues,complete=not issues)
        try:
            with zipfile.ZipFile(path) as z:
                members=[i for i in z.infolist() if not i.is_dir()];total=0
                if len(members)>max_members:issues.append(dict(locator='ZIP',reason='member_budget',members_available=len(members),members_read=max_members))
                for info in members[:max_members]:
                    member=info.filename.replace('\\','/');p=PurePosixPath(member)
                    if p.is_absolute() or '..' in p.parts or re.match(r'^[A-Za-z]:',member):issues.append(dict(locator=member,reason='unsafe_member_path'));continue
                    if info.flag_bits&1:issues.append(dict(locator=member,reason='encrypted_member'));continue
                    suffix=PurePosixPath(member).suffix.casefold()
                    if suffix not in {'.pdf','.docx','.xlsx'}:
                        issues.append(dict(locator=member,reason='unsupported_member_format'));continue
                    if info.file_size>max_member_bytes or total+info.file_size>max_total_bytes or info.file_size>max(1,info.compress_size)*200:
                        issues.append(dict(locator=member,reason='archive_expansion_budget'));continue
                    body=z.read(info);total+=len(body)
                    if suffix=='.pdf':
                        if not body.startswith(b'%PDF'):issues.append(dict(locator=member,reason='missing_pdf_magic'));continue
                        pdf(body,'ZIP member: '+member)
                    elif not office(body,'ZIP member: '+member):issues.append(dict(locator=member,reason='missing_office_structure'))
        except (zipfile.BadZipFile,RuntimeError,NotImplementedError) as e:issues.append(dict(locator='ZIP',reason='invalid_or_unsupported_archive',error=type(e).__name__))
    else:issues.append(dict(locator=path.name,reason='unsupported_document_format'))
    return dict(parts=parts,issues=issues,complete=not issues)

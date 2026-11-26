"""Contextual multilingual scope screening. Terms alone never establish a live opportunity."""
from __future__ import annotations
import re, unicodedata
from lxml import html,etree

def plain(value):
    if not value:return ''
    try:
        root=html.fromstring(str(value))
        for node in root.xpath('//script|//style'):node.drop_tree()
        value=root.text_content()
    except (ValueError,TypeError,etree.ParserError):pass
    return ' '.join(str(value).split())

def norm(value):
    value=plain(value).casefold().replace('ı','i')
    # Thai prefixes are often attached directly to English product names. Both are Unicode
    # word characters, so a regex \b alone cannot find the start of the English name.
    value=re.sub(r'(?<=[\u0e00-\u0e7f])(?=[a-z0-9])|(?<=[a-z0-9])(?=[\u0e00-\u0e7f])',' ',value)
    return ''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c) or '\u0e00'<=c<='\u0e7f')

LINE=re.compile(r'\b(?:overhead|aerial)\s+(?:(?:power|electric(?:al)?|transmission|distribution)\s+)*(?:line|conductors?|cables?|network)|\b(?:power|electric(?:al|ity)?|transmission|distribution)\s+(?:(?:transmission|distribution)\s+)?lines?\b|\breconduct(?:or|oring)|\b(?:linhas?|lineas?)\s+(?:(?:aereas?|electricas?|primarias?|secundarias?)\s*)?(?:de\s+)?(?:transmissao|transmision|distribuicao|distribucion|(?:alta|media|baixa|baja)\s+tens[aio]o?n)|\bredes?\s+(?:aereas?|primarias?|secundarias?|de\s+distribui(?:cao|cion))|\b(?:enerji\s+(?:iletim|nakil)\s+hatt|havai\s+(?:hat|enerji)|eih\b|sutt\b|sutet\b|jaringan\s+(?:transmisi|distribusi)|saluran\s+udara)|สายส่ง|สายจำหน่าย',re.I)
INSULATOR=re.compile(r'\b(?:insulators?|isoladores?|aisladores?|izolator\w*)\b|ลูกถ้วย')
ELECTRIC=re.compile(r'\b(?:electri\w*|eletric\w*|enerji|kv|kilovolt|substation\w*|subestac\w*|transmission|poste\w*|conductors?|condutor(?:es)?|glass|vidrio|vidro|suspension|suspensao|porcelana|porselen|cap.and.pin)\b|ไฟฟ้า')
FITTING=re.compile(r'\b(?:line hardware|line fittings|ferragens?|herrajes|crossarms?|cruzetas?|armatures?|steel.core|acsr|aaac|guy wire|parallel groove clamp|compression connector)\b|สายอลูมิเนียมแกนเหล็ก|สายอลูมิเนียม|สายอะลูมิเนียม')
WEAK=re.compile(r'\b(?:electrification|eletrificacao|electrificacion|substation\w*|subestac\w*|transmission and distribution|(?:electric(?:al|ity)?|eletric[ao]?)\s+(?:network|transmission)|rede\s+eletrica)\b|\bgardu\b')
NON_GRID=re.compile(r'\b(?:overhead\s+(?:scanner|projector|cost|expense)|scanners?\s+(?:planetarios?|overhead)|canoa\s+havaiana|porta\s+de\s+vidro|door\s+hardware|playground|banheiros?\s+quimicos?|palco|gaming\s+grid|dental|odontolog\w*|impressoras?|printers?|photocopiers?)\b')
BUILDING=re.compile(r'\b(?:lavanderia|hospital|escola|edificios?|building|pisos?|internal wiring|instalacoes\s+internas)\b')
UNDERGROUND=re.compile(r'\b(?:underground|subterrane\w*|cable\s+connection|submarine|submarin\w*)\b|ใต้ดิน')
OVERHEAD=re.compile(r'\b(?:overhead|aerial|aere[ao]s?|havai|saluran\s+udara|sutt|sutet)\b|สายส่งอากาศ|สายป้อนอากาศ|สายจำหน่ายอากาศ')
ELECTRICAL_INSULATOR=re.compile(r'\b(?:(?:pin[ -]?post|spool|strain|suspension|disc|cap[ -]and[ -]pin)\s+insulators?|insulators?\s+(?:class\s+5[2367]|52[ -]))\b')
LINE_PART=re.compile(r'\b(?:(?:full[ -]?tension|non[ -]?tension)\s+sleeves?|(?:steel|wood|concrete|polymeric)\s+crossarms?|(?:aluminum|aluminium|copper|acsr|aaac)\s+(?:wire\s+)?conductors?|(?:strain|suspension)\s+clamps?)\b')
NETWORK_WORK=re.compile(r'\b(?:rebuild|reconstruct\w*|reconduct\w*|replace|replacement|upgrad\w*|construct\w*|reabilit\w*|reform\w*|reforc\w*|ampliac\w*|mejoram\w*|creacion|instal\w*)\b')
NETWORK_CONTEXT=re.compile(r'\b(?:feeders?|alimentador\w*|wood poles?|concrete poles?|servicio de (?:energia electrica|suministro electrico))\b|\b(?:og|ag|yg)\s*[/ -]\s*(?:og|ag|yg)\b[^.;]{0,60}\bsebeke\b|ปักเสา|พาดสาย|สายป้อนอากาศ')

def assess(title,description=None):
    t=norm(title);d=norm(description);body=t+' '+d
    def result(verdict,reason):return dict(verdict=verdict,reason=reason,method='context_rules_v3; mixed-script and network vocabulary; not LLM',confirmed_product_fit=False,overhead_or_product_explicit=bool(OVERHEAD.search(body) or INSULATOR.search(body) and (ELECTRIC.search(body) or 'ลูกถ้วย' in body) or 'guy wire' in body or FITTING.search(body) and ELECTRIC.search(body) or ELECTRICAL_INSULATOR.search(body) or LINE_PART.search(body)))
    # Check electrical context in the tender scope, never infer it from a buyer name/footer.
    line=bool(LINE.search(body) and (ELECTRIC.search(body) or re.search(r'\blinhas?\s+de\s+transmissao|\blineas?\s+de\s+transmision|\boverhead\s+(?:line|conductors?)|\breconduct\w*|\b(?:linha|linea)\s+aerea|\b(?:power|transmission)\s+line|\b(?:eih|sutt|sutet)\b|enerji\s+(?:iletim|nakil)|สายส่ง|สายจำหน่าย',body)));ins=bool(INSULATOR.search(body) and ELECTRIC.search(body));fitting=bool(FITTING.search(body) and ELECTRIC.search(body))
    # These are specific electrical line components, unlike generic hardware or a buyer name.
    ins=ins or 'ลูกถ้วย' in body
    fitting=fitting or 'guy wire' in body
    ins=ins or bool(ELECTRICAL_INSULATOR.search(body))
    fitting=fitting or bool(LINE_PART.search(body))
    if re.search(r'\b(?:hydraulic cable cutter|cardiac monitor|heart monitor)\b',t):return result('excluded','Tool/medical equipment purchase; line-department reference does not establish line construction')
    if re.search(r'rede[s]? de distribuicao de (?:agua|gas)|abastecimento de agua|pocos?\s+tubulares?|sistema de bombeamento',t) and not re.search(r'(?:rede|linha)[^.;]{0,35}(?:energia eletrica|media tensao|aerea|transmissao)',body):return result('excluded','Water/gas infrastructure without an explicit electric-line scope')
    if re.search(r'fotovoltaic|microgeracao|minigeracao',t) and not re.search(r'(?:construcao|ampliacao|implantacao|remanejamento|extensao)[^.;]{0,55}(?:rede|linha)',body):return result('needs_documents','Solar generation/connection alone does not establish construction of a grid line')
    if NON_GRID.search(t) and not LINE.search(t):return result('excluded','Explicit unrelated equipment/work in project title')
    if UNDERGROUND.search(t) and not OVERHEAD.search(t) and not ins:return result('excluded','Exclusively underground/submarine title; no OHL or insulator scope established')
    if re.search(r'\b(?:linha|linea)\s+aerea\b',body) :line=True
    if re.search(r'(?:ampliacion|construccion|reconstruccion)[^.;]{0,70}sistema de transporte de energia electrica',body):line=True
    if line and re.search(r'manutencao (?:corretiva|preventiva)|preventive maintenance|routine maintenance',t) and not re.search(r'reconstr|substitui|recondut|restabelec|reabilita|remanej',t):return result('needs_documents','Routine maintenance title; major line reconstruction/replacement not established')
    if line or ins or fitting:return result('relevant','Explicit electric-line, electrical insulator or line-component scope')
    if NETWORK_CONTEXT.search(body) and (NETWORK_WORK.search(body) or re.search(r'\b(?:yapim|tesis|tesisi)\b|ปักเสา|พาดสาย',body)):
        return result('needs_documents','Feeder/pole/network-service construction wording; exact OHL scope requires review')
    if re.search(r'\bslt\b',body) and ELECTRIC.search(body):return result('needs_documents','Official electrical project code SLT; exact line/component scope requires verification')
    if WEAK.search(body):
        if BUILDING.search(t):return result('excluded','Building electrical works without line/component scope')
        return result('needs_documents','Electric network/sector context exists; external line scope needs specifications')
    return result('excluded','No contextual evidence of electric lines, insulators or line components')

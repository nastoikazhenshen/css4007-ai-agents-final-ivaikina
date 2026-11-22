"""Broad candidate screening. Final relevance belongs to the central agent."""
import re

EQUIPMENT = re.compile(r"insulators?|isolator|glass suspension|"
                       r"line hardware|conductors?|konduktor|opgw|ground wire|"
                       r"transmission towers?|menara listrik|tiang listrik|line fittings|arrester", re.I)
GRID = re.compile(r"\b(?:sutet|sutt|sutm|sutr|sktt|sktm|sktr|jtm|jtr|hvdc|hvac)\b|"
                  r"transmission (?:line|network|system)|transmisi (?:listrik|tenaga)|"
                  r"distribution (?:line|network|system)|"
                  r"jaringan (?:listrik|distribusi|tegangan|transmisi)|"
                  r"gardu induk|\bsubstation|power (?:grid|line|distribution|transmission)|"
                  r"electri(?:c|cal|city)|tenaga listrik|listrik perdesaan|"
                  r"pembangkit|hydropower|\bhepp\b|\bplts\b|"
                  r"overhead|underground cable", re.I)
LINE_CONTEXT = re.compile(r"\b(?:sutet|sutt|sutm|sutr|sktt|sktm|sktr|jtm|jtr|hvdc|hvac)\b|"
                          r"(?:power|electricity|electric) transmission|transmission lines?|"
                          r"distribution (?:lines?|networks?)|power lines?|"
                          r"jaringan (?:listrik|tegangan|transmisi)|"
                          r"saluran (?:udara|kabel)|overhead lines?", re.I)
OTHER_NETWORK = re.compile(r"jaringan pipa|gas pipeline|water (?:supply|treatment)|\b(?:spam|nuwsp)\b|"
                           r"telekomunikasi|fiber opti[ck]|sewerage|drainage|irrigation", re.I)


def candidate_reason(title, description="", project=""):
    if EQUIPMENT.search(title):
        return "equipment_in_title"
    if GRID.search(title) and not OTHER_NETWORK.search(title):
        return "energy_or_grid_in_title"
    if GRID.search(project) and not OTHER_NETWORK.search(project):
        return "energy_project_context_requires_review"
    if OTHER_NETWORK.search(title):
        return None
    for sentence in re.split(r"(?<=[.;])\s+|[\r\n]+", description):
        if len(sentence) <= 1400 and (EQUIPMENT.search(sentence) or LINE_CONTEXT.search(sentence)) and not OTHER_NETWORK.search(sentence):
            return "energy_mention_in_description_requires_review"
    return None

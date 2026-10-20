"""Conservative section and entry grouping. All facts retain real source anchors."""
import copy
import re
import unicodedata

from app.schemas.extraction import CandidateDraft, FieldEvidence, SourceReference

HEADING_GROUPS = {
    "career_objective": "Summary|Professional Summary|Profile|Profile Summary|Career Summary|Career Objective|Objective|Professional Objective|Job Objective|About Me|Overview|Personal Statement|Executive Summary|Career Profile|Introduction",
    "skills": "Skills|Technical Skills|Core Skills|Key Skills|Relevant Skills|Areas of Expertise|Core Competencies|Expertise|Tools|Programming Languages|Frameworks|Databases|Cloud Technologies|Technologies|Tools and Technologies|Programming Skills|Technical Proficiencies|Competencies|Skills and Interests",
    "education": "Education|Academic Background|Academic Qualifications|Educational Qualification|Qualifications|Degree|Academic History",
    "experience": "Experience|Work Experience|Professional Experience|Employment History|Employment|Internship|Internship Experience|Internships|Industrial Training|Experience and Internships",
    "projects": "Projects|Academic Projects|Personal Projects|Technical Projects|Major Projects|Project Work|Selected Projects|Relevant Projects",
    "certifications": "Certifications|Certificates|Courses|Training|Professional Certifications|Online Courses",
    "achievements": "Achievements|Awards|Honors|Accomplishments|Extra-Curricular Achievements",
    "publications": "Publications|Research Publications|Papers|Research Papers",
    "links": "Links|Profiles|Online Profiles|Portfolio|Social Links|Contact|Contact Information",
    "additional_sections": "Research|Volunteer Experience|Volunteer Work|Leadership|Leadership Experience|Extracurricular Activities|Extracurricular|Languages|Interests|References|Hobbies|Volunteering|Activities|Additional Information",
    "preferred_roles": "Preferred Roles|Target Roles",
}


def normalize(value):
    return " ".join(str(value).casefold().split())


def heading_key(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("&", "and")
    text = re.sub(r"^\s*(?:\(?\d+\)?[.)\-:]?|[ivxlcdm]+[.)])\s*", "", text)
    return re.sub(r"[^a-z]", "", text)


SECTIONS = {heading_key(alias): section for section, aliases in HEADING_GROUPS.items() for alias in aliases.split("|")}
STOP_HEADINGS = {heading_key(s) for s in ["References", "Languages", "Interests", "Hobbies", "Personal Details", "Declaration", "Volunteering", "Activities", "Additional Information"]}
TECH_LABEL = re.compile(r"^(?:technologies(?:/tools)?|tech(?:nology)? stack|tools(?: and technologies)?|built with|programming languages|languages|frameworks|databases|skills)\s*:\s*(.*)", re.I)
DEGREE = re.compile(r"\b(?:b\.?\s*tech|m\.?\s*tech|bachelor|master|degree|diploma|b\.?\s*sc|m\.?\s*sc|b\.?\s*e\.?\b|ph\.?\s*d|bca|mca|mba|class\s+(?:xii|xi|x|12|10))\b", re.I)
DATE = r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+)?(?:19|20)\d{2}"
DATE_RANGE = re.compile(rf"({DATE})\s*(?:[-–—]|\bto\b)\s*({DATE}|Present|Current|Ongoing)", re.I)
URL = re.compile(r"(?:https?://|www\.)[^\s<>|]+", re.I)
# An explicit vocabulary, never technologies inferred from a role. Preserve matched spelling.
TECH = re.compile(r"(?<![\w])(?:Python|JavaScript|TypeScript|Java|C\+\+|C#|React Three Fiber|React Native|React(?:\.js)?|Next\.js|Node(?:\.js)?|FastAPI|Django|Flask|PostgreSQL|MySQL|SQL|MongoDB|SQLite|Docker|Kubernetes|Git|GitHub Actions|HTML|CSS|Tailwind(?: CSS)?|AWS|Azure|TensorFlow|PyTorch|Pandas|NumPy|Power BI|Excel)(?![\w])", re.I)
BULLET = re.compile(r"^\s*(?:[•●▪◦*\-–\x80-\x9f](?:\s+|$)|\d+[.)]\s+)")
ACTION = re.compile(r"^(?:built|developed|created|implemented|designed|led|worked|responsible|contributed|improved|managed|achieved|reduced|increased|won|awarded|using|used|integrated|deployed|maintained|supported|tested|wrote|a\s|an\s|the\s)", re.I)
ABSENT = re.compile(r"^(?:none|not applicable|n/?a|no (?:projects|experience|skills)(?: yet)?)[.!]?$", re.I)


def leaves(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            result.update(leaves(child, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(leaves(child, f"{prefix}.{index}"))
    elif value != "" and value is not None:
        result[prefix] = value
    return result


def detect_heading(line):
    raw = line.strip().strip("#*_• ")
    first, sep, tail = raw.partition(":")
    key = heading_key(first if sep else raw)
    if key in SECTIONS:
        return SECTIONS[key], first.strip(), tail.strip() if sep else ""
    if key in STOP_HEADINGS:
        return "unclassified", first.strip(), ""
    return None


def segment(blocks):
    """Keep order, blank/bullet boundaries and column-local section state."""
    sections, states = [], {}
    for block in blocks:
        column = (block.get("page"), "right" if block.get("position", {}).get("x", 0) > block.get("page_width", 600) * .5 else "left") if block.get("reading_order_uncertain") else "main"
        for line_number, raw in enumerate(block["text"].splitlines() or [""], 1):
            if block.get("cells"):
                if line_number > 1:
                    continue
                raw = " | ".join(cell.replace("\n", " ") for cell in block["cells"])
            line = BULLET.sub("", raw).strip()
            heading = detect_heading(line)
            # Technology labels inside an entry belong to that entry, not a new skills section.
            current = states.get(column)
            # Languages: is a category inside Technical Skills, not an instruction
            # to discard the rest of that section.
            if current is not None and current['field']=='skills' and ':' in line and heading and heading[0] in {'unclassified','additional_sections'}:
                heading=None
            if not heading and current is not None and current['field'] in {'contact','links'} and block.get('native_line') and len(line.split())>=9 and re.search(r'\b(?:undergraduate|graduate|student|specializing|professional|experienced)\b',line,re.I) and not re.search(r'@|https?://',line):
                heading=('career_objective','',line)
            if not heading and block.get('section_hint'):
                heading=(block['section_hint'],line,'')
            if not heading and block.get('structural_heading'):
                heading=('unclassified',line,'')
            if not heading and current is not None and re.fullmatch(r"heading\s*1", block.get("style", ""), re.I) and len(line.split()) <= 8:
                # Only a real top-level paragraph heading can stop an unknown section.
                # Bold body text and short lines alone are never section headings.
                heading = ("unclassified", line, "")
            technology_label = TECH_LABEL.match(line)
            if current is not None and current["field"] in {"projects", "experience"} and technology_label and technology_label[1].strip() and not re.match(r"^skills\s*:", line, re.I):
                heading = None
            if current is not None and current["field"] == "experience" and re.match(r"^(achievements|responsibilities)\s*:", line, re.I):
                heading = None
            if heading:
                field, title, tail = heading
                current = {"field": field, "heading": title, "heading_block": block, "records": [], "column": column}
                sections.append(current); states[column] = current
                if not tail:
                    continue
                line = tail
            elif current is None:
                current = {"field": "contact", "heading": "", "heading_block": block, "records": [], "column": column}
                sections.append(current); states[column] = current
            current["records"].append({"text": line, "raw": raw, "block": block, "line": line_number,
                "bullet": bool(BULLET.match(raw) or block.get("list_item")), "section": current["field"], "heading": current["heading"], "section_inferred": bool(current["heading_block"].get("section_hint"))})
    return sections


def reference(record, quote=None):
    block = record["block"]
    return SourceReference(block_id=block["block_id"], page=block.get("page"), paragraph=block.get("paragraph"),
        line=record.get("line"), position=block.get("position"), quote=(quote if quote is not None else block["text"] if block.get("cells") else record["raw"])[:2000]).model_dump()


def statement_type(heading):
    key = heading_key(heading)
    return "objective" if "objective" in key else "summary" if "summary" in key else "profile_statement"


def deterministic_extract(blocks):
    values, evidence = CandidateDraft().model_dump(), {}
    sections = segment(blocks)
    warnings = ["Limited local extraction: ambiguous layouts may be missed. Confidence labels are heuristic, not calibrated probabilities."]
    if any(block.get("reading_order_uncertain") for block in blocks):
        warnings.append("Possible multi-column layout: reading order is uncertain. Review each source block; columns were kept separate.")

    def put(path, value, record, *, confidence="medium", warning=None, normalized=False, group="", refs=None, original=None):
        ambiguous = confidence == "low" or bool(record["block"].get("reading_order_uncertain")) or record.get("section_inferred",False)
        evidence[path] = FieldEvidence(source=reference(record), references=refs or [], parser_version="3", confidence="low" if ambiguous else confidence,
            warning=warning or ("Section bucket inferred from source-supported visual layout; confirm its meaning." if record.get("section_inferred") else "Reading order is uncertain; check the source." if ambiguous else None),
            classification="ambiguous" if ambiguous else "normalized" if normalized else "directly_found",
            source_section=record["section"], original_text=original if original is not None else str(value), heading=record["heading"], group_id=group,
            statement_type=statement_type(record["heading"]) if path == "career_objective" else "").model_dump()

    def scalar(field, value, record, **kw):
        if value and not values[field]:
            values[field] = value
            put(field, value, record, **kw)

    def add_skill(name, record, **kw):
        original = name
        name = " ".join(name.strip().split()).rstrip(".;")
        if not name or len(name) > 160 or len(values["skills"]) >= 100:
            return
        if normalize(name) not in [normalize(v) for v in values["skills"]]:
            index = len(values["skills"]); values["skills"].append(name)
            put(f"skills.{index}", name, record, normalized=name != original.strip(), original=original.strip(), **kw)

    def technologies(text, record, explicit=False):
        tokens = re.split(r"[,;|•·/]", text) if explicit else [m.group() for m in TECH.finditer(text)]
        result = []
        for token in tokens:
            token = token.strip().rstrip(".")
            if token and not ABSENT.fullmatch(token) and len(token) <= 160 and normalize(token) not in [normalize(v) for v in result]:
                result.append(token); add_skill(token, record)
        return result[:40]

    def append_label(field, text, record):
        if text and len(text) <= 160 and len(values[field]) < (20 if field == "preferred_roles" else 30) and normalize(text) not in [normalize(v) for v in values[field]]:
            index = len(values[field]); values[field].append(text); put(f"{field}.{index}", text, record)

    records = [r for s in sections for r in s["records"] if r["text"]]
    # Contact recognition does not produce arbitrary summary/project facts.
    for record in records:
        line = record["text"]
        email = re.search(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", line)
        if email:
            scalar("email", email.group()[:254], record, confidence="high")
        for match in URL.finditer(line):
            url = match.group().rstrip(".,;)")
            if not url.lower().startswith("http"):
                url = "https://" + url
            if len(url) <= 500 and url not in values["links"] and len(values["links"]) < 30:
                index = len(values["links"]); values["links"].append(url); put(f"links.{index}", url, record, normalized=match.group().startswith("www."))
        phone = re.search(r"(?:\+\d{1,3}[ -]?)?(?:\(?\d{2,4}\)?[ -]?)?\d[\d ()-]{7,}\d", line)
        if phone and 10 <= len(re.sub(r"\D", "", phone.group())) <= 15 and not DATE_RANGE.search(phone.group()):
            scalar("phone", phone.group().strip()[:60], record)
        label = re.match(r"^(name|full name|location|address)\s*:\s*(.+)", line, re.I)
        if label and record["section"] in {"contact", "links"}:
            field = "name" if "name" in label[1].lower() else "location"
            scalar(field, label[2][:120 if field == "name" else 160], record)
    first = next((r for r in records if r["section"] == "contact"), None)
    if first and not values["name"] and 2 <= len(first["text"].split()) <= 5 and not re.search(r"[@:/\d]|resume|curriculum|linkedin|github", first["text"], re.I):
        scalar("name", first["text"][:120], first, confidence="low", warning="Possible name from the first contact line. Confirm its meaning.")

    # A dedicated statement extractor: stop at a recognized boundary, keep all lines.
    statements = [s for s in sections if s["field"] == "career_objective"]
    for section in statements:
        lines = [r for r in section["records"] if r["text"]]
        if lines and not values["career_objective"]:
            text = "\n".join(r["text"] for r in lines)[:2000]
            scalar("career_objective", text, lines[0], refs=[reference(r) for r in lines])
    if not values["career_objective"]:
        warnings.append("Profile Summary / Objective not detected. No profile statement was generated from unrelated text.")
    elif len(statements) > 1:
        warnings.append("Multiple profile-statement sections found; the first nonempty section is shown. Review the source heading.")

    for section in sections:
        field = section["field"]
        entries = section["records"]
        if field == "unclassified" and section["heading"]:
            warnings.append("An additional section was kept separate from profile facts; review unrecognized headings manually.")
        if field == "skills":
            for record in entries:
                line = record["text"]
                if not line:
                    continue
                if ABSENT.fullmatch(line):
                    continue
                line = re.sub(r"^[^:]{1,40}:\s*", "", line)
                for token in re.split(r"[,;|•·/]|\t+", line.replace('C/C++','C∕C++')):
                    token=token.replace('C∕C++','C/C++')
                    add_skill(token, record)
        elif field in {"certifications", "achievements", "publications", "preferred_roles"}:
            for record in entries:
                parts = re.split(r"[,;|]", record["text"]) if field == "preferred_roles" else [record["text"]]
                for part in parts:
                    append_label(field, part.strip(), record)
                if field == "certifications":
                    technologies(record["text"], record)
        elif field in {"projects", "experience", "education"}:
            parse_entries(field, entries, values, put, technologies, warnings)
    missing = [field for field in ("education", "skills", "experience", "projects") if not values[field]]
    if missing:
        warnings.append("Not detected: " + ", ".join(missing) + ". Missing evidence does not mean you lack these qualifications.")
    return CandidateDraft.model_validate(values).model_dump(), evidence, list(dict.fromkeys(warnings))


def parse_entries(field, records, values, put, technologies, warnings):
    from app.schemas.extraction import EducationDraft, ExperienceDraft, ProjectDraft
    model = {"education": EducationDraft, "experience": ExperienceDraft, "projects": ProjectDraft}[field]
    current, index, group, last_blank = None, -1, "", True
    period_records = {}
    table_columns = []
    pending_header=None
    column_names = {"degree": "qualification", "program": "qualification", "qualification": "qualification", "institution": "institution", "university": "institution", "period": "period", "dates": "period", "location": "location", "cgpa": "grade", "grade": "grade", "percentage": "grade", "specialization": "specialization", "startyear": "start_year", "endyear": "end_year", "expectedgraduation": "expected_graduation"}

    def assign(key, value, record, confidence="medium", warning=None):
        if not value or current is None:
            return
        normalized = False
        if key in {"url", "links"}:
            def safe_url(url):
                return "https://" + url if url.lower().startswith("www.") else url
            converted = [safe_url(url) for url in value] if isinstance(value, list) else safe_url(value)
            normalized, value = converted != value, converted
        if isinstance(value, list):
            for item in value:
                if len(current[key]) >= (40 if key == "technologies" else 10 if key == "links" else 30):
                    break
                if key=='technologies' and any(re.search(r'(?<!\w)'+re.escape(item)+r'(?!\w)',existing,re.I) for existing in current[key]):
                    continue
                if normalize(item) not in [normalize(v) for v in current[key]]:
                    i = len(current[key]); current[key].append(item)
                    put(f"{field}.{index}.{key}.{i}", item, record, confidence=confidence, warning=warning, group=group, normalized=normalized)
        else:
            current[key] = value
            if key == "period":
                period_records[index] = record
            put(f"{field}.{index}.{key}", value, record, confidence=confidence, warning=warning, group=group, normalized=normalized)

    def description(text, record):
        if not text or current is None:
            return
        previous = current["description"]
        current["description"] = (previous + "\n" + text).strip()[:2000]
        path = f"{field}.{index}.description"
        # A source reference per constituent line, never a fabricated combined quote.
        refs = [] if not previous else copy.deepcopy(proof_for.get(path, []))
        refs.append(reference(record))
        proof_for[path] = refs
        put(path, current["description"], record, refs=refs, group=group)

    proof_for = {}
    for record_index,record in enumerate(records):
        text = record["text"]
        if not text:
            last_blank = True
            continue
        if ABSENT.fullmatch(text):
            current = None
            continue
        next_record=next((r for r in records[record_index+1:] if r['text'].strip()),None)
        role_pattern=r'\b(?:intern|engineer|developer|analyst|assistant|manager|designer|researcher|consultant|trainee)\b'
        if field=='education' and not record['bullet'] and not DEGREE.search(text) and re.search(r'\b(?:university|college|institute|school)\b',text,re.I) and not re.match(r'^(?:institution|university)\s*:',text,re.I) and next_record and DEGREE.search(next_record['text']):
            pending_header=record
            continue
        if field=='experience' and not record['bullet'] and next_record and re.search(role_pattern,next_record['text'],re.I) and not ACTION.match(text) and not re.match(r'^[^:]{1,40}:',text) and not re.search(role_pattern,text,re.I) and len(text)<160:
            pending_header=record
            continue
        raw_parts = [part.strip() for part in re.split(r"\s*\|\s*|\t+|\s+/\s+(?=https?://)", text)]
        if field == "education" and len(raw_parts) > 1 and sum(heading_key(part) in column_names for part in raw_parts) >= 2:
            table_columns = [column_names.get(heading_key(part), "") for part in raw_parts]
            continue
        row_values = dict(zip(table_columns, raw_parts)) if field == "education" and table_columns and len(raw_parts) == len(table_columns) else {}
        tech_match = TECH_LABEL.match(text)
        labeled = re.match(r"^(project(?: name| title)?|title|role|contribution|organization|company|institution|university|degree|qualification|location|date(?:s)?|duration|period|speciali[sz]ation|cgpa|grade|percentage|expected graduation|responsibilities|achievements|outcomes?|results?|repository|deployment|deployment url|live demo)\s*:\s*(.+)", text, re.I)
        date = DATE_RANGE.search(text)
        url_only = bool(URL.match(text))
        start = False
        parts = list(raw_parts)
        if field == "experience" and len(parts) > 1:
            if not re.search(role_pattern, parts[0], re.I) and re.search(role_pattern, parts[1], re.I):
                parts[0], parts[1] = parts[1], parts[0]
        if field == "education" and len(parts) > 1:
            degree_index = next((i for i, part in enumerate(parts) if DEGREE.search(part)), None)
            if degree_index is not None and degree_index != 0:
                parts.insert(0, parts.pop(degree_index))
        uncertain = False
        if field == "projects":
            stack_line=bool(record['block'].get('native_line') and current is not None and not current['description'] and not record['bullet'] and not tech_match and not labeled and not url_only and not ACTION.match(text) and ',' in parts[0] and TECH.search(parts[0]) and len(text.split())<=25)
            link_labels=bool(re.fullmatch(r'(?:GitHub|Live Demo|Video|Portfolio|Hugging Face)(?:\s*\|\s*(?:GitHub|Live Demo|Video|Portfolio))*',text,re.I))
            title_label = labeled and labeled[1].lower() in {"project", "project name", "project title", "title"}
            prose_label = re.match(r"^([^:]{2,120}):\s+(.+)", text) if not tech_match and not labeled else None
            spaced_title=bool(record['block'].get('native_line') and record['block'].get('gap_before',0)>record['block'].get('position',{}).get('font_size',10)*1.7)
            plausible_title = not record["bullet"] and not tech_match and not labeled and not url_only and not ACTION.match(text) and not re.search(r"[.!?]$", text) and len(text) <= 160 and (len(text.split()) <= 12 or spaced_title)
            start = bool(title_label or prose_label or (len(parts) > 1 and not tech_match and not labeled and not record["bullet"]) or plausible_title)
            if title_label:
                parts = [labeled[2]]
            elif prose_label:
                parts = [prose_label[1], prose_label[2]]
            if record["bullet"] and current is None and not ACTION.match(text) and len(text.split()) <= 10 and not tech_match:
                start, uncertain = True, True
            if start and (ACTION.match(parts[0]) or heading_key(parts[0]) in {"projectname", "title", "description", "technologies"}):
                start = False
            if stack_line or link_labels or (date and date.group()==text) or re.fullmatch(DATE,text,re.I):start=False
            if link_labels:continue
            if stack_line:
                stack=re.sub(r'\s+(?:GitHub|Live Demo|Video|Hugging Face)(?:\s*\|.*)?$','',text,flags=re.I)
                assign('technologies',technologies(stack,record,explicit=True),record)
                continue
        elif field == "experience":
            # Explicit role/company pairs, or a clearly labeled title. Never infer employer.
            at = re.match(r"^(.{2,120}?)\s+(?:at|@)\s+(.+)", text, re.I)
            role_label = labeled and labeled[1].lower() in {"role", "title"}
            titleish = bool(re.search(role_pattern, parts[0], re.I))
            start = bool(not record["bullet"] and not tech_match and (at or (len(parts) >= 2 and titleish) or role_label or (titleish and len(text.split()) <= 8)))
            if at:
                parts = [at[1], at[2]]
            elif role_label:
                parts = [labeled[2]]
        else:
            start = bool(DEGREE.search(parts[0]) and not record["bullet"] and not re.search(r"certificate|workshop|course|certification", parts[0], re.I))
            if labeled and labeled[1].lower() in {"degree", "qualification"}:
                start, parts = bool(DEGREE.search(labeled[2])), [labeled[2]]
        if start:
            if len(values[field]) >= (30 if field == "projects" else 20):
                warnings.append(f"Entry limit reached for {field}; review omitted content manually.")
                break
            current = model().model_dump(); index = len(values[field]); values[field].append(current)
            group = f"{field}:{record['block']['block_id']}:{record['line']}"
            if field == "projects":
                assign("name", parts[0].strip()[:160], record, "low" if uncertain else "medium", "Uncertain project title/grouping; review before confirming." if uncertain else None)
                for part in parts[1:]:
                    if DATE_RANGE.search(part) or re.fullmatch(DATE, part, re.I):
                        assign("period", part[:80], record)
                    elif URL.search(part):
                        links = [m.group().rstrip(".,;)") for m in URL.finditer(part)]
                        assign("url", links[0], record); assign("links", links, record)
                    elif not ACTION.match(part) and all(TECH.fullmatch(t.strip()) for t in re.split(r"[,;/]", part)):
                        assign("technologies", technologies(part, record, explicit=True), record)
                    else:
                        description(part, record)
                        assign("technologies", technologies(part, record), record)
            elif field == "experience":
                assign("role", parts[0][:160], record)
                if pending_header:
                    org=re.split(r'\s+[—–-]\s+(?=Remote\b)',pending_header['text'],maxsplit=1)[0]
                    assign('organization',org,pending_header)
                    if org!=pending_header['text']:assign('location','Remote',pending_header)
                    pending_header=None
                for part in parts[1:]:
                    if DATE_RANGE.search(part) or re.fullmatch(DATE, part, re.I):
                        assign("period", part[:80], record)
                    elif not current['organization']:
                        assign('organization',part[:160],record)
                    else:
                        assign("location", part[:160], record)
            else:
                qualification=re.split(r',?\s*(?:CGPA|CGPI|GPA|Grade)\s*:',parts[0],maxsplit=1,flags=re.I)[0].rstrip(', ')
                assign("qualification", qualification[:160], record)
                if pending_header:
                    assign('institution',pending_header['text'][:160],pending_header)
                    pending_header=None
                if len(parts) > 1 and not current['institution'] and not DATE_RANGE.search(parts[1]) and not re.search(r'%|cgpa|cgpi|gpa|grade|expected|semester',parts[1],re.I):
                    assign("institution", parts[1][:160], record)
                for part in parts[1:]:
                    if DATE_RANGE.search(part) or re.fullmatch(DATE, part, re.I):
                        assign("period", part[:80], record)
                    elif re.search(r"cgpa|gpa|grade|%|percentage", part, re.I):
                        assign("grade", part[:80], record)
                    elif re.search(r"expected|graduat", part, re.I):
                        graduation=re.search(r'(?:expected\s+graduation|graduating|graduation)\s*[:\-]?\s*(?:19|20)\d{2}',part,re.I)
                        assign("expected_graduation", graduation.group() if graduation else part[:80], record)
                grade=re.search(r'(?:CGPA|CGPI|GPA|Grade)\s*:\s*[\d.]+',text,re.I)
                if grade:assign('grade',grade.group(),record)
                for key, value in row_values.items():
                    if key in current and value:
                        assign(key, value[:160 if key in {"qualification", "institution", "location", "specialization"} else 80], record)
            last_blank = False
        elif current is not None:
            if tech_match and field in {"projects", "experience"}:
                explicit_tools=technologies(tech_match[1], record, explicit=True)
                assign("technologies", explicit_tools, record)
                if field=='projects' and re.match(r'^tools\s*:',text,re.I):assign('tools',explicit_tools,record)
            elif labeled:
                key = labeled[1].lower()
                target = {"role": "role", "contribution": "role", "repository":"repository", "deployment":"deployment_url", "deployment url":"deployment_url", "live demo":"deployment_url", "company": "organization", "organization": "organization", "institution": "institution", "university": "institution", "location": "location", "date": "period", "dates": "period", "duration": "period", "period": "period", "specialization": "specialization", "specialisation": "specialization", "cgpa": "grade", "grade": "grade", "percentage": "grade", "expected graduation": "expected_graduation"}.get(key)
                if target in current:
                    assign(target, labeled[2][:160 if target not in {"grade", "period", "expected_graduation"} else 80], record)
                elif key in {"outcome", "outcomes", "result", "results"} and field == "projects":
                    assign('outcomes',[labeled[2]],record)
                elif key in {"responsibilities", "achievements"} and field == "experience":
                    assign(key, [labeled[2]], record)
            elif url_only and field == "projects":
                links = [m.group().rstrip(".,;)") for m in URL.finditer(text)]
                if not current["url"]:
                    assign("url", links[0], record)
                assign("links", links, record)
            elif (date or re.fullmatch(DATE,text,re.I)) and len(text.split()) <= 8:
                assign("period", (date.group() if date else text)[:80], record)
            elif field in {"experience", "projects"}:
                description(text, record)
                assign("technologies", technologies(text, record), record)
                if field == "experience":
                    assign("achievements" if re.match(r"^(won|awarded|achieved|increased|reduced)\b", text, re.I) else "responsibilities", [text[:2000]], record)
            elif field == "education" and not current["institution"] and re.search(r"university|college|institute|school", text, re.I):
                assign("institution", text[:160], record)
        elif record["text"]:
            warnings.append(f"Unassociated {field} text was not converted to an entry; review this section manually.")
        if current is not None:
            period = current.get("period", "")
            match = DATE_RANGE.search(period)
            if match:
                date_record = period_records.get(index, record)
                if field == "education":
                    years = re.findall(r"(?:19|20)\d{2}", match.group())
                    if years:
                        assign("start_year", years[0], date_record)
                    if len(years) > 1:
                        assign("end_year", years[1], date_record)
                elif field == "experience":
                    assign("start_date", match[1], date_record); assign("end_date", match[2], date_record)
                    if match[2].lower() in {"present", "current", "ongoing"}:
                        assign("current_status", match[2], date_record)
            last_blank = False
    for entry in values[field]:
        if not entry.get("period"):
            warnings.append(f"{field.capitalize()}: dates not detected for one or more entries; no dates were inferred.")
        required = {"education": "institution", "experience": "organization", "projects": "name"}[field]
        if not entry.get(required):
            warnings.append(f"{field.capitalize()}: {required} not detected; complete or deselect this entry before confirmation.")


def anchored_ai_values(proposal, blocks, *, decisions=None):
    """Validate source text AND section/entry association, not merely substring presence."""
    values = proposal.values.model_dump()
    local, local_evidence, _ = deterministic_extract(blocks)
    by_id = {block["block_id"]: block for block in blocks}
    block_sections = {}
    for section in segment(blocks):
        for record in section["records"]:
            block_sections.setdefault(record["block"]["block_id"], set()).add(section["field"])
    claims = {claim.path: claim for claim in proposal.claims}
    validated, proof, rejected = {}, {}, 0
    for path, value in leaves(values).items():
        claim = claims.get(path)
        block = by_id.get(claim.block_id) if claim else None
        field = path.split(".")[0]
        supported = bool(claim and block and not claim.inferred and claim.quote.strip() and normalize(claim.quote) in normalize(block["text"]) and normalize(value).removeprefix("https://").removeprefix("http://") in normalize(claim.quote))
        reason='source_literal_not_supported' if not supported else ''
        group = ""
        if supported and field in {"projects", "education", "experience"}:
            anchor = "name" if field == "projects" else "qualification" if field == "education" else "role"
            entry = values[field][int(path.split(".")[1])]
            matching = [i for i, item in enumerate(local[field]) if normalize(item[anchor]) == normalize(entry[anchor]) and entry[anchor]]
            qualifier = "institution" if field == "education" else "organization" if field == "experience" else "url"
            if entry.get(qualifier):
                matching = [i for i in matching if normalize(local[field][i].get(qualifier, "")) == normalize(entry[qualifier])]
            if len(matching) != 1:
                matching = []  # Duplicate titles/degrees/roles are not interchangeable.
            candidates = [p for p in local_evidence.values() if p.get("source", {}).get("block_id") == claim.block_id]
            groups = {local_evidence.get(f"{field}.{i}.{anchor}", {}).get("group_id") for i in matching}
            same = next((p for p in candidates if p.get("group_id") in groups and p.get("group_id")
                and any(normalize(claim.quote) in normalize(ref["quote"]) for ref in ([p["source"]] if p.get("source") else []) + p.get("references", []))), None)
            supported = bool(field in block_sections.get(claim.block_id, set()) and same)
            if not supported:reason='entry_or_section_not_supported'
            if supported and '.technologies.' in path and any(normalize(value)!=normalize(token) and normalize(value) in normalize(token) for i in matching for token in local[field][i].get('technologies',[])):
                supported=False;reason='technology_name_fragment'
            group = same["group_id"] if same else ""
        elif supported and field == "career_objective":
            supported = "career_objective" in block_sections.get(claim.block_id, set())
            if not supported:reason='statement_section_not_supported'
        elif supported and field == "skills":
            supported = any(normalize(value) == normalize(skill) for skill in local["skills"]) or bool(re.search(r"(?:skills|technologies|tools)\s*:", block["text"], re.I))
            if not supported:reason='skill_not_explicitly_supported'
        if decisions is not None:
            decisions.append({'path':path,'block_id':claim.block_id if claim else None,'status':'accepted' if supported else 'withheld','reason':reason})
        if not supported:
            rejected += 1
            continue
        validated[path] = value
        heading = next((s["heading"] for s in segment(blocks) if s["field"] == field and any(r["block"]["block_id"] == claim.block_id for r in s["records"])), "")
        proof[path] = FieldEvidence(source=SourceReference(block_id=claim.block_id, page=block.get("page"), paragraph=block.get("paragraph"), line=claim.line, position=block.get("position"), quote=claim.quote),
            confidence=claim.confidence, parser_version="3", classification="ambiguous" if claim.confidence == "low" else claim.classification, method="groq", warning=claim.warning,
            original_text=str(value), source_section=field, group_id=group, heading=heading,
            statement_type=statement_type(heading) if field == "career_objective" else "").model_dump()
    cleaned, cleaned_proof = CandidateDraft().model_dump(), {}
    def rebuild(item, prefix, target):
        if isinstance(item, dict):
            return {key: rebuild(child, f"{prefix}.{key}", f"{target}.{key}") for key, child in item.items()}
        if isinstance(item, list):
            output = []
            for i, child in enumerate(item):
                old, new = f"{prefix}.{i}", f"{target}.{len(output)}"
                if not any(p == old or p.startswith(old + ".") for p in validated):
                    continue
                output.append(rebuild(child, old, new))
            return output
        if prefix in validated:
            cleaned_proof[target] = proof[prefix]
            return validated[prefix]
        return ""
    for field, value in values.items():
        if isinstance(value, list):
            cleaned[field] = rebuild(value, field, field)
        elif field in validated:
            cleaned[field] = value; cleaned_proof[field] = proof[field]
    return CandidateDraft.model_validate(cleaned).model_dump(), cleaned_proof, rejected


def merge_enhancement(local, local_evidence, enhanced, enhanced_evidence):
    values, evidence = copy.deepcopy(local), copy.deepcopy(local_evidence)
    for field, value in enhanced.items():
        if not value:
            continue
        if not isinstance(value, list):
            # Preserve a full local statement; don't replace it with a model summary.
            if field == "career_objective" and values[field]:
                continue
            if values[field] and normalize(values[field]) != normalize(value):
                continue  # Conflicting readings are retained separately in the audit.
            values[field] = value
            if field in enhanced_evidence:
                evidence[field] = enhanced_evidence[field]
            continue
        for old_index, item in enumerate(value):
            if any(normalize(item) == normalize(existing) for existing in values[field]):
                continue
            if field in {"projects", "education", "experience"}:
                anchor = "name" if field == "projects" else "qualification" if field == "education" else "role"
                matching = next((i for i, existing in enumerate(values[field]) if normalize(item[anchor]) == normalize(existing[anchor])), None)
                if matching is not None:
                    # Enrich missing facts only within the validated existing group.
                    # A model never replaces source wording or combines separate entries.
                    existing = values[field][matching]
                    for key, child in item.items():
                        old_prefix, new_prefix = f"{field}.{old_index}.{key}", f"{field}.{matching}.{key}"
                        if isinstance(child, list):
                            for j, token in enumerate(child):
                                path = f"{old_prefix}.{j}"
                                if token not in existing[key] and path in enhanced_evidence:
                                    target = f"{new_prefix}.{len(existing[key])}"
                                    existing[key].append(token); evidence[target] = enhanced_evidence[path]
                        elif child and not existing.get(key) and old_prefix in enhanced_evidence:
                            existing[key] = child; evidence[new_prefix] = enhanced_evidence[old_prefix]
                    continue
            new_index = len(values[field]); values[field].append(item)
            for path in leaves(item, f"{field}.{old_index}"):
                if path in enhanced_evidence:
                    evidence[path.replace(f"{field}.{old_index}", f"{field}.{new_index}", 1)] = enhanced_evidence[path]
    return CandidateDraft.model_validate(values).model_dump(), evidence

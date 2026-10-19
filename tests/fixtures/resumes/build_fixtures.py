"""Generate synthetic resumes and explicit evaluation oracles. No personal data."""
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from xml.sax.saxutils import escape
import json
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

ROOT = Path(__file__).parent
CASES = []
def case(name, text, expected, projects=None, technologies=None, skills=None, warning=None):
    path = ROOT / (name + '.txt'); path.write_text(text)
    CASES.append({'file': path.name, 'expected': expected, 'project_names': projects or [],
        'project_technologies': technologies or [], 'skills': skills, 'warning': warning})

case('one_column', '''Nisha Example
Email: nisha@example.com
Location: Aligarh, India
Summary:
Computer engineering student building useful services.
Skills:
Python, SQL
Education:
B.Tech Computer Engineering | Example University | 2023-2027
Projects:
Student Portal | Built an academic portal using Python
''', {'name':'Nisha Example','email':'nisha@example.com','location':'Aligarh, India',
'career_objective':'Computer engineering student building useful services.','education.0.qualification':'B.Tech Computer Engineering','education.0.institution':'Example University','education.0.start_year':'2023','education.0.end_year':'2027','projects.0.description':'Built an academic portal using Python'}, ['Student Portal'], [['Python']], ['Python','SQL'])
case('objective_only', '''Nisha Example
01. CAREER OBJECTIVE:
To develop useful student services.
Seeking an internship in backend development.
2) TECHNICAL SKILLS:
Python; FastAPI
''', {'career_objective':'To develop useful student services.\nSeeking an internship in backend development.'}, skills=['Python','FastAPI'])
case('profile_section', '''Nisha Example
Profile
Engineering student with an interest in accessible software.
Experience:
Backend Intern | Example Labs | Jun 2025-Aug 2025
- Built APIs using FastAPI.
''', {'career_objective':'Engineering student with an interest in accessible software.','experience.0.role':'Backend Intern','experience.0.organization':'Example Labs','experience.0.start_date':'Jun 2025','experience.0.end_date':'Aug 2025'}, skills=['FastAPI'])
case('multiple_projects', '''Nisha Example
Academic Projects:
Study Planner | React, FastAPI | 2025
Technologies: React, FastAPI
- Built a planner for students.
- Deployed an API.
Budget Tracker: Tracks spending for students.
Technologies: Python, SQLite
- Added monthly charts.
''', {'projects.0.period':'2025','projects.0.description':'Built a planner for students.\nDeployed an API.','projects.1.description':'Tracks spending for students.\nAdded monthly charts.'}, ['Study Planner','Budget Tracker'], [['React','FastAPI'],['Python','SQLite']], ['React','FastAPI','Python','SQLite'])
case('project_technologies', '''Nisha Example
SELECTED PROJECTS
Campus Map / https://github.com/example/campus-map
Technologies: JavaScript, React
Role: Frontend contributor
- Built a campus directory.
Library Service | Python, PostgreSQL | Jan 2025-Mar 2025
Technologies: Python, PostgreSQL
https://example.com/library
- Built library APIs.
''', {'projects.0.url':'https://github.com/example/campus-map','projects.0.role':'Frontend contributor','projects.1.period':'Jan 2025-Mar 2025','projects.1.url':'https://example.com/library'}, ['Campus Map','Library Service'], [['JavaScript','React'],['Python','PostgreSQL']], ['JavaScript','React','Python','PostgreSQL'])
case('work_entries', '''Nisha Example
Professional Experience
Backend Intern | Example Labs | Jun 2025-Aug 2025 | Aligarh
- Built APIs using FastAPI.
Achievements: Reduced API latency by 20%.
Software Developer at Sample Systems
Jan 2026-Present
Technologies: Java, PostgreSQL
- Maintained billing services.
Projects:
Attendance Tool: Built attendance charts using Python.
''', {'experience.0.organization':'Example Labs','experience.0.location':'Aligarh','experience.0.achievements.0':'Reduced API latency by 20%.','experience.1.organization':'Sample Systems','experience.1.start_date':'Jan 2026','experience.1.end_date':'Present','experience.1.current_status':'Present'}, ['Attendance Tool'], [['Python']], ['FastAPI','Java','PostgreSQL','Python'])
case('no_projects', '''Nisha Example
Work Experience:
Project Engineer | Example Labs | 2025-2026
- Built an internal dashboard using React.
Certifications:
Python Fundamentals
''', {'experience.0.role':'Project Engineer','experience.0.organization':'Example Labs','certifications.0':'Python Fundamentals'}, skills=['React','Python'], warning='Not detected:')
case('no_statement', '''Nisha Example
Email: nisha@example.com
Skills:
Python, SQL
Education:
B.Tech | Example University
''', {'career_objective':'','education.0.qualification':'B.Tech','education.0.institution':'Example University'}, skills=['Python','SQL'], warning='Profile Summary / Objective not detected')
case('duplicate_skills', '''Nisha Example
Core Skills:
Python, python; SQL | Python |  Machine   Learning
Technologies: SQL, React
Certifications:
Python Fundamentals
''', {}, skills=['Python','SQL','Machine Learning','React'])
case('unusual_headings', '''Nisha Example
I. P R O F E S S I O N A L   O B J E C T I V E —
To build reliable student tools.
02) TOOLS & TECHNOLOGIES:
Python; SQL
03. RELEVANT PROJECTS:
Exam Planner: Built a scheduling tool using Python.
04. EXTRA-CURRICULAR ACHIEVEMENTS:
University hackathon finalist
REFERENCES
Available on request
''', {'career_objective':'To build reliable student tools.','achievements.0':'University hackathon finalist'}, ['Exam Planner'], [['Python']], ['Python','SQL'])
case('missing_dates', '''Nisha Example
Education:
B.Tech Computer Science
Institution: Example University
Specialization: Software Engineering
CGPA: 8.6/10
Experience:
Backend Intern
Company: Example Labs
- Built APIs using FastAPI.
Projects:
Study Service
Technologies: Python, FastAPI
- Created student study groups.
''', {'education.0.period':'','education.0.grade':'8.6/10','education.0.specialization':'Software Engineering','experience.0.organization':'Example Labs','experience.0.start_date':'','projects.0.period':''}, ['Study Service'], [['Python','FastAPI']], ['FastAPI','Python'], warning='dates not detected')
case('mixed_bullets', '''Nisha Example
Professional Summary:
Student building accessible software.
Seeking practical engineering experience.
Projects:
Project Name: Accessible Notes
Technologies: React, TypeScript
- Built a note editor.
Tracks notes and categories.
• Added keyboard navigation.
Project Name: Course Catalog
Technologies: Python, SQLite
- Built a course search API.
Courses:
Introduction to Algorithms
Skills:
Git, SQL
''', {'career_objective':'Student building accessible software.\nSeeking practical engineering experience.','projects.0.description':'Built a note editor.\nTracks notes and categories.\nAdded keyboard navigation.'}, ['Accessible Notes','Course Catalog'], [['React','TypeScript'],['Python','SQLite']], ['React','TypeScript','Python','SQLite','Git','SQL'])

# DOCX table rows with explicit headers, plus paragraph formatting/list metadata.
xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Nisha Example</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Academic Background</w:t></w:r></w:p>
<w:tbl>'''
for cells in [['Degree','Institution','Period','CGPA','Location'], ['B.Tech Computer Science','Example University','2023-2027','8.6/10','Aligarh']]:
    xml += '<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>'+escape(c)+'</w:t></w:r></w:p></w:tc>' for c in cells) + '</w:tr>'
xml += '</w:tbl><w:p><w:r><w:t>Skills:</w:t></w:r></w:p><w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>Python</w:t></w:r></w:p></w:body></w:document>'
with ZipFile(ROOT/'table_education.docx','w') as archive:
    archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    archive.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    archive.writestr('word/_rels/document.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    archive.writestr('word/styles.xml','<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>')
    archive.writestr('word/document.xml',xml)
CASES.append({'file':'table_education.docx','expected':{'education.0.qualification':'B.Tech Computer Science','education.0.institution':'Example University','education.0.grade':'8.6/10','education.0.location':'Aligarh','education.0.start_year':'2023','education.0.end_year':'2027'},'project_names':[],'project_technologies':[], 'skills':['Python']})

# Separate PDF text columns with overlapping vertical ranges.
def pdf(path, columns):
    writer = PdfWriter(); page = writer.add_blank_page(width=612,height=792)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
    commands=[]
    for x,lines in columns:
        commands.append(f'BT /F1 11 Tf {x} 740 Td')
        for line in lines:
            safe=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            commands.append(f'({safe}) Tj 0 -18 Td')
        commands.append('ET')
    if not columns:
        image=DecodedStreamObject(); image.set_data(bytes([245,245,245,30,30,30,30,30,30,245,245,245]))
        image.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):__import__('pypdf').generic.NumberObject(2),NameObject('/Height'):__import__('pypdf').generic.NumberObject(2),NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):__import__('pypdf').generic.NumberObject(8)})
        page['/Resources'][NameObject('/XObject')]=DictionaryObject({NameObject('/Im1'):writer._add_object(image)})
        commands.append('q 200 0 0 200 40 540 cm /Im1 Do Q')
    stream=DecodedStreamObject(); stream.set_data('\n'.join(commands).encode()); page[NameObject('/Contents')]=writer._add_object(stream)
    with open(path,'wb') as file: writer.write(file)
pdf(ROOT/'two_column.pdf',[(40,['Nisha Example','Skills:','Python, SQL','Education:','B.Tech | Example University | 2023-2027']), (330,['Projects:','Notes App','Technologies: React','Built a note editor.'])])
CASES.append({'file':'two_column.pdf','expected':{'education.0.qualification':'B.Tech','education.0.institution':'Example University'},'project_names':['Notes App'],'project_technologies':[['React']], 'skills':['Python','SQL','React'], 'warning':'multi-column'})
pdf(ROOT/'scanned.pdf',[])
CASES.append({'file':'scanned.pdf','error':'no_readable_text','expected':{},'project_names':[],'project_technologies':[], 'skills':[]})
(ROOT/'cases.json').write_text(json.dumps(CASES,indent=2)+'\n')
print(f'Generated {len(CASES)} synthetic resume fixtures.')

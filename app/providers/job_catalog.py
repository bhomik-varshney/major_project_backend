"""Authored role recipes. The generator varies coherent recipes, not unrelated skills."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RoleRecipe:
    category: str
    title: str
    industry: str
    department: str
    skills: tuple[str, ...]
    tools: tuple[str, ...]
    task: str


def recipe(category, title, industry, department, skills, tools, task):
    return RoleRecipe(category, title, industry, department, tuple(skills.split('|')), tuple(tools.split('|')), task)


ROLES = (
    recipe('software_engineering','Software Engineer','Software services','Engineering','Python|Git|Data structures|SQL|REST APIs','Git|pytest|VS Code','build and test small product features'),
    recipe('frontend','Frontend Developer','Digital products','Web engineering','React|TypeScript|HTML|CSS|Accessibility|JavaScript','Vite|Playwright|Figma','implement accessible and responsive interfaces'),
    recipe('backend','Backend Developer','Cloud software','Platform engineering','Python|FastAPI|REST APIs|SQL|Docker|PostgreSQL','Docker|pytest|PostgreSQL','develop validated REST endpoints and database queries'),
    recipe('full_stack','Full-Stack Developer','Digital products','Product engineering','JavaScript|React|Node.js|SQL|Git|REST APIs','Node.js|PostgreSQL|Git','deliver web features across the interface and API'),
    recipe('mobile','Mobile Developer','Consumer technology','Mobile engineering','Kotlin|Android|REST APIs|Git|Unit testing','Android Studio|Gradle|Firebase','build and test mobile application screens'),
    recipe('web','Web Developer','Web services','Web engineering','HTML|CSS|JavaScript|Accessibility|Git','VS Code|Chrome DevTools|Git','build usable websites and check browser compatibility'),
    recipe('data_analysis','Data Analyst','Analytics services','Analytics','SQL|Python|Statistics|Pandas|Data visualization|Excel','Pandas|Excel|Matplotlib','clean datasets and explain product trends'),
    recipe('data_science','Data Scientist','Analytics services','Data science','Python|Statistics|Pandas|SQL|Machine learning','Jupyter|scikit-learn|Pandas','evaluate reproducible statistical models'),
    recipe('machine_learning','Machine Learning Engineer','AI products','Machine learning','Python|Machine learning|Pandas|Model evaluation|Statistics','PyTorch|scikit-learn|MLflow','compare baseline models and document evaluation results'),
    recipe('artificial_intelligence','AI Engineer','AI products','Applied AI','Python|Machine learning|REST APIs|Model evaluation|Responsible AI','PyTorch|FastAPI|Jupyter','prototype evaluated AI services with human review'),
    recipe('natural_language_processing','NLP Engineer','Language technology','Language research','Python|Natural language processing|Machine learning|Text processing|Model evaluation','spaCy|Transformers|Jupyter','evaluate text classification and retrieval experiments'),
    recipe('computer_vision','Computer Vision Engineer','Imaging technology','Vision research','Python|Computer vision|Image processing|Machine learning|Linear algebra','OpenCV|PyTorch|NumPy','measure image model performance on approved datasets'),
    recipe('cybersecurity','Security Analyst','Security services','Security operations','Networking|Linux|Threat analysis|Incident response|Security monitoring','Wireshark|Nmap|SIEM','triage lab security alerts and document findings'),
    recipe('cloud','Cloud Engineer','Cloud services','Cloud infrastructure','Linux|Networking|AWS|Docker|Python','AWS|Terraform|Docker','maintain supervised cloud development environments'),
    recipe('devops','DevOps Engineer','Cloud services','Developer infrastructure','Linux|Git|Docker|CI/CD|Python|Kubernetes','Docker|GitHub Actions|Terraform','improve container builds and deployment pipelines'),
    recipe('site_reliability','Site Reliability Engineer','Cloud software','Reliability','Linux|Python|Monitoring|Networking|Incident response','Prometheus|Grafana|Docker','investigate service health and improve operational runbooks'),
    recipe('database','Database Administrator','Enterprise technology','Data platform','SQL|PostgreSQL|Database backups|Linux|Query optimization','PostgreSQL|pgAdmin|Docker','test database backups and review query plans'),
    recipe('quality_assurance','QA Engineer','Software services','Quality assurance','Software testing|Test cases|Bug reporting|SQL|Git','Postman|Jira|SQL','design test cases and report reproducible defects'),
    recipe('test_automation','Automation Test Engineer','Software services','Test engineering','Python|Software testing|Playwright|Git|REST APIs','Playwright|pytest|Postman','automate browser and API regression tests'),
    recipe('ui_ux','UI/UX Designer','Design services','User experience','User research|Wireframing|Accessibility|Usability testing|Interaction design','Figma|FigJam|Miro','research user needs and test interface prototypes'),
    recipe('product_design','Product Designer','Digital products','Product design','Interaction design|Design systems|Prototyping|Accessibility|User research','Figma|FigJam|Miro','iterate on accessible product design systems'),
    recipe('product_management','Product Manager','Digital products','Product','Product discovery|Requirements analysis|Data analysis|Communication|Prioritization','Jira|Miro|Excel','document user problems and prioritize small product improvements'),
    recipe('business_analysis','Business Analyst','Business services','Business analysis','Requirements analysis|SQL|Excel|Process mapping|Communication','Excel|Jira|Power BI','map business processes and validate stakeholder requirements'),
    recipe('project_management','Project Coordinator','Technology consulting','Delivery','Project planning|Communication|Risk tracking|Agile|Documentation','Jira|Excel|Confluence','coordinate delivery milestones and maintain risk records'),
    recipe('technical_writing','Technical Writer','Developer tools','Documentation','Technical writing|Markdown|Git|REST APIs|Information architecture','Git|Markdown|OpenAPI','write clear tutorials and validate API documentation'),
    recipe('research','Research Assistant','Research services','Research','Research methods|Python|Statistics|Technical writing|Data analysis','Jupyter|Zotero|Python','run supervised experiments and record reproducible methods'),
    recipe('embedded','Embedded Systems Engineer','Electronics','Embedded engineering','C/C++|Microcontrollers|Sensors|RTOS|Electronics','STM32|FreeRTOS|Logic analyzer','test microcontroller firmware and sensor interfaces'),
    recipe('iot','IoT Engineer','Connected devices','IoT engineering','C/C++|Sensors|MQTT|Networking|Python','ESP32|MQTT|Arduino','connect sensor prototypes and validate telemetry'),
    recipe('robotics','Robotics Engineer','Industrial automation','Robotics','Python|C/C++|Robotics|Control systems|Sensors','ROS 2|Gazebo|OpenCV','simulate robot behaviors and evaluate sensor feedback'),
    recipe('networking','Network Engineer','Telecommunications','Network operations','Networking|TCP/IP|Linux|Routing|Troubleshooting','Wireshark|Cisco Packet Tracer|Linux','troubleshoot supervised lab networks'),
    recipe('it_support','IT Support Specialist','IT services','IT support','Troubleshooting|Windows|Networking|Customer support|Documentation','ServiceNow|Windows|Linux','resolve support tickets and maintain troubleshooting guides'),
    recipe('systems','Systems Administrator','IT services','Systems operations','Linux|Windows|Bash|Networking|Database backups','Linux|PowerShell|Ansible','maintain development systems and test recovery procedures'),
    recipe('fintech','Fintech Developer','Financial technology','Payments engineering','Python|SQL|REST APIs|Unit testing|Data validation','FastAPI|PostgreSQL|Git','test payment simulations and reconcile synthetic transaction data'),
    recipe('martech','Marketing Technology Analyst','Marketing technology','Marketing systems','SQL|Data analysis|CRM|Marketing automation|Excel','HubSpot|SQL|Excel','validate campaign data flows and CRM integrations'),
    recipe('digital_marketing','Digital Marketing Associate','Marketing services','Digital marketing','SEO|Content writing|Web analytics|Communication|Excel','Google Analytics|Search Console|Excel','analyze synthetic campaign metrics and draft content plans'),
    recipe('business_intelligence','Business Intelligence Analyst','Analytics services','Business intelligence','SQL|Power BI|Data modeling|Excel|Data visualization','Power BI|SQL|Excel','build reviewed dashboards and document metric definitions'),
    recipe('operations','Operations Analyst','Business services','Operations','Excel|Process improvement|Data analysis|Communication|SQL','Excel|Power BI|SQL','measure process bottlenecks and document operational improvements'),
    recipe('hr_technology','HR Technology Analyst','HR technology','People systems','Excel|HRIS|Data validation|Communication|SQL','Workday Sandbox|Excel|SQL','test people-system workflows using synthetic employee data'),
    recipe('healthtech','Healthcare Software Developer','Healthcare technology','Health software','Python|SQL|REST APIs|Data validation|Unit testing','FastAPI|PostgreSQL|pytest','develop test services using synthetic healthcare records'),
    recipe('edtech','Education Technology Developer','Education technology','Learning products','JavaScript|React|Accessibility|REST APIs|SQL','React|Node.js|Git','build accessible learning interfaces with sample course data'),
    recipe('ecommerce','E-commerce Developer','E-commerce','Commerce engineering','JavaScript|React|SQL|REST APIs|Software testing','Node.js|PostgreSQL|Playwright','implement catalog and checkout simulations'),
    recipe('game_development','Game Developer','Games','Gameplay engineering','C#|Unity|Game development|Linear algebra|Git','Unity|Blender|Git','prototype gameplay mechanics and measure frame performance'),
    recipe('blockchain','Blockchain Developer','Web3 technology','Distributed systems','Solidity|JavaScript|Smart contracts|Software testing|Git','Hardhat|Solidity|Node.js','test smart contracts in a local development network'),
    recipe('gis','GIS Analyst','Geospatial technology','Geospatial analysis','GIS|Python|Spatial analysis|SQL|Data visualization','QGIS|PostGIS|GeoPandas','analyze sample spatial datasets and document map accuracy'),
    recipe('renewable_energy','Energy Technology Analyst','Renewable energy','Energy analytics','Python|Data analysis|Excel|Statistics|Energy systems','Pandas|Excel|MATLAB','analyze synthetic energy-production and efficiency data'),
    recipe('developer_relations','Developer Relations Associate','Developer tools','Developer education','Technical writing|Communication|Git|REST APIs|Python','Git|Markdown|Postman','prepare tested developer examples and collect product feedback'),
)

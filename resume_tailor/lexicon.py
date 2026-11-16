"""Word lists used by the deterministic ATS engine: skills, synonyms, action verbs, section names."""

from __future__ import annotations

# --- Common skills / tools, used by the offline (no-AI) JD keyword extractor -------------------
_SKILLS_RAW = """
Python|Java|JavaScript|TypeScript|C++|C#|Go|Golang|Rust|Ruby|PHP|Kotlin|Swift|Scala|R|MATLAB|Perl|Bash|Shell scripting|PowerShell|Dart|Objective-C|Elixir|Haskell|Lua|Julia|Solidity
SQL|NoSQL|PostgreSQL|MySQL|SQLite|Oracle|Microsoft SQL Server|MongoDB|Redis|Cassandra|DynamoDB|Elasticsearch|Neo4j|Snowflake|BigQuery|Redshift|Databricks|ClickHouse|Firebase|Supabase|CockroachDB
HTML|CSS|Sass|Tailwind CSS|Bootstrap|React|React Native|Redux|Next.js|Vue.js|Nuxt|Angular|Svelte|jQuery|Webpack|Vite|Node.js|Express|NestJS|Deno|GraphQL|REST APIs|gRPC|WebSockets|Flutter|SwiftUI|Jetpack Compose
Django|Flask|FastAPI|Spring Boot|Spring|Hibernate|.NET|ASP.NET|Ruby on Rails|Laravel|Celery|RabbitMQ|Kafka|Apache Spark|PySpark|Hadoop|Airflow|dbt|Flink|Beam|Kinesis|Pub/Sub|SQS|SNS
AWS|Azure|Google Cloud|GCP|EC2|S3|Lambda|ECS|EKS|CloudFormation|CloudWatch|IAM|Terraform|Ansible|Pulumi|Chef|Puppet|Docker|Kubernetes|Helm|OpenShift|Istio|Serverless|Microservices|Linux|Unix|Nginx|Apache
CI/CD|Jenkins|GitHub Actions|GitLab CI|CircleCI|Travis CI|Argo CD|Git|GitHub|GitLab|Bitbucket|Jira|Confluence|Prometheus|Grafana|Datadog|New Relic|Splunk|ELK|Sentry|PagerDuty|OpenTelemetry
Machine Learning|Deep Learning|Artificial Intelligence|Natural Language Processing|Computer Vision|Generative AI|Large Language Models|LLMs|RAG|Prompt Engineering|Fine-tuning|Reinforcement Learning|MLOps|Feature Engineering|Model Deployment|Recommendation Systems|Time Series|Forecasting
TensorFlow|PyTorch|Keras|scikit-learn|XGBoost|LightGBM|Hugging Face|LangChain|LlamaIndex|OpenCV|spaCy|NLTK|Pandas|NumPy|SciPy|Matplotlib|Seaborn|Plotly|Jupyter|MLflow|Kubeflow|SageMaker|Vertex AI|Vector databases|Pinecone|FAISS
Data Analysis|Data Analytics|Data Engineering|Data Science|Data Visualization|Data Modeling|Data Warehousing|Data Pipelines|ETL|ELT|Data Governance|Data Quality|Statistics|Statistical Analysis|Hypothesis Testing|A/B Testing|Experimentation|Regression|Predictive Modeling|Big Data
Tableau|Power BI|Looker|Excel|Google Sheets|VBA|SAS|SPSS|Stata|Alteryx|Qlik|Google Analytics|Mixpanel|Amplitude|Segment|Salesforce|HubSpot|SAP|Workday|NetSuite|Zendesk|ServiceNow
Unit Testing|Integration Testing|Test Automation|Selenium|Cypress|Playwright|Jest|Mocha|PyTest|JUnit|TDD|BDD|QA|Performance Testing|Load Testing|Postman|Appium
System Design|Distributed Systems|Scalability|High Availability|Performance Optimization|Caching|Load Balancing|Event-Driven Architecture|Domain-Driven Design|Design Patterns|Object-Oriented Programming|Functional Programming|Data Structures|Algorithms|Concurrency|Multithreading|API Design|Software Architecture|Cloud Architecture
Security|Cybersecurity|OAuth|JWT|SSO|Encryption|Penetration Testing|SIEM|SOC 2|ISO 27001|GDPR|HIPAA|PCI DSS|Zero Trust|IAM policies|Vulnerability Management|Threat Modeling|Network Security
Agile|Scrum|Kanban|Waterfall|SDLC|Lean|Six Sigma|Project Management|Program Management|Product Management|Product Strategy|Roadmapping|Stakeholder Management|Requirements Gathering|User Stories|Sprint Planning|Change Management|Risk Management|Budgeting|Vendor Management
UX|UI|User Research|Usability Testing|Wireframing|Prototyping|Figma|Sketch|Adobe XD|Adobe Photoshop|Adobe Illustrator|InDesign|Design Systems|Accessibility|WCAG|Responsive Design|Interaction Design|Information Architecture
SEO|SEM|Content Marketing|Email Marketing|Social Media Marketing|Digital Marketing|Performance Marketing|Growth Marketing|Marketing Automation|Google Ads|Meta Ads|Copywriting|Brand Strategy|Market Research|Competitive Analysis|Lead Generation|CRM|Customer Acquisition|Conversion Rate Optimization
Financial Modeling|Financial Analysis|FP&A|Forecasting|Valuation|Accounting|GAAP|IFRS|Auditing|Bookkeeping|Tax|Payroll|Accounts Payable|Accounts Receivable|Reconciliation|QuickBooks|Investment Analysis|Risk Analysis|Due Diligence
Sales|B2B|B2C|SaaS|Account Management|Business Development|Customer Success|Negotiation|Pipeline Management|Cold Outreach|Customer Retention|Upselling|Partnerships|Go-to-Market
Communication|Leadership|Collaboration|Teamwork|Problem Solving|Critical Thinking|Mentoring|Coaching|Cross-functional|Ownership|Attention to Detail|Time Management|Adaptability|Presentation Skills|Decision Making|Strategic Thinking|Customer Focus|Analytical Skills
Mobile Development|iOS|Android|Web Development|Full Stack|Frontend|Backend|DevOps|SRE|Site Reliability Engineering|Embedded Systems|IoT|Blockchain|Game Development|Unity|Unreal Engine|AR/VR|Robotics|ROS|FPGA|Verilog|PLC
"""

SKILLS: list[str] = []
for _line in _SKILLS_RAW.strip().splitlines():
    for _term in _line.split("|"):
        _term = _term.strip()
        if _term and _term not in SKILLS:
            SKILLS.append(_term)

SOFT_SKILLS = {
    "communication", "leadership", "collaboration", "teamwork", "problem solving", "critical thinking",
    "mentoring", "coaching", "cross-functional", "ownership", "attention to detail", "time management",
    "adaptability", "presentation skills", "decision making", "strategic thinking", "customer focus",
    "analytical skills", "stakeholder management", "negotiation",
}

CERT_HINTS = ("certified", "certification", "certificate", "pmp", "cpa", "cfa", "cissp", "ccna", "aws certified")

# --- Synonym groups: every member counts as a match for every other member -------------------
_SYNONYM_GROUPS = [
    ["javascript", "js", "ecmascript"],
    ["typescript", "ts"],
    ["kubernetes", "k8s"],
    ["aws", "amazon web services"],
    ["gcp", "google cloud", "google cloud platform"],
    ["azure", "microsoft azure"],
    ["machine learning", "ml"],
    ["artificial intelligence", "ai"],
    ["natural language processing", "nlp"],
    ["ci/cd", "cicd", "ci cd", "continuous integration", "continuous delivery", "continuous deployment"],
    ["rest apis", "rest api", "restful apis", "restful api", "restful", "rest"],
    ["postgresql", "postgres"],
    ["mongodb", "mongo"],
    ["react", "react.js", "reactjs"],
    ["vue.js", "vue", "vuejs"],
    ["angular", "angularjs"],
    ["node.js", "nodejs"],
    ["next.js", "nextjs"],
    ["go", "golang"],
    [".net", "dotnet", ".net core"],
    ["c#", "csharp"],
    ["microsoft sql server", "sql server", "mssql"],
    ["power bi", "powerbi"],
    ["excel", "microsoft excel", "ms excel"],
    ["a/b testing", "ab testing", "split testing", "a/b tests"],
    ["ux", "user experience"],
    ["ui", "user interface"],
    ["qa", "quality assurance"],
    ["seo", "search engine optimization"],
    ["kpis", "kpi", "key performance indicators"],
    ["okrs", "okr", "objectives and key results"],
    ["sdlc", "software development life cycle", "software development lifecycle"],
    ["object-oriented programming", "oop", "object oriented programming", "object-oriented design"],
    ["large language models", "llms", "llm"],
    ["generative ai", "genai", "gen ai"],
    ["etl", "extract, transform, load", "extract transform load"],
    ["crm", "customer relationship management"],
    ["iac", "infrastructure as code"],
    ["tdd", "test-driven development", "test driven development"],
    ["s3", "amazon s3"],
    ["ec2", "amazon ec2"],
    ["scikit-learn", "sklearn", "scikit learn"],
    ["pytorch", "torch"],
    ["data visualization", "data viz", "data visualisation"],
    ["sre", "site reliability engineering"],
    ["apache spark", "spark"],
    ["apache kafka", "kafka"],
    ["airflow", "apache airflow"],
    ["gcp bigquery", "bigquery"],
    ["github actions", "gh actions"],
    ["unit testing", "unit tests"],
    ["microservices", "microservice architecture", "micro-services"],
    ["cross-functional", "cross functional", "cross-team"],
    ["problem solving", "problem-solving"],
    ["fp&a", "financial planning and analysis", "financial planning & analysis"],
    ["b2b", "business-to-business"],
    ["saas", "software as a service"],
]

SYNONYMS: dict[str, set[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _member in _group:
        SYNONYMS.setdefault(_member, set()).update(m for m in _group if m != _member)

# --- Action verbs (past tense). Present tense is derived at lookup time. ----------------------
ACTION_VERBS = set(
    """
    accelerated accomplished achieved acquired adapted addressed administered advanced advised advocated
    aligned allocated analyzed analysed answered anticipated applied appointed appraised approved architected
    arranged assembled assessed attained audited augmented authored automated balanced benchmarked boosted
    briefed broadened budgeted built calculated campaigned captured cataloged centralized chaired championed
    clarified classified closed coached coded collaborated collected combined commissioned communicated
    compiled completed composed computed conceived conceptualized condensed conducted configured consolidated
    constructed consulted containerized contracted contributed controlled converted convinced coordinated
    corrected crafted created cultivated curated customized cut debugged decreased defined delegated delivered
    demonstrated deployed designed detected determined developed devised diagnosed digitized directed
    discovered dispatched documented doubled drafted drove earned edited educated eliminated empowered enabled
    encouraged engaged engineered enhanced enlarged ensured established estimated evaluated examined exceeded
    executed expanded expedited experimented explored exported extracted fabricated facilitated finalized
    fixed forecasted forged formalized formed formulated fostered founded generated governed graduated grew
    guided halved handled harmonized headed hired identified illustrated implemented improved incorporated
    increased influenced informed initiated innovated inspected inspired installed instituted instructed
    integrated interpreted interviewed introduced invented investigated launched led leveraged lifted
    lowered maintained managed mapped marketed mastered maximized measured mediated mentored merged migrated
    minimized mobilized modeled modelled moderated modernized modified monitored motivated navigated
    negotiated nurtured observed obtained onboarded opened operated optimized orchestrated organized
    originated outperformed overhauled oversaw owned packaged partnered performed persuaded piloted pioneered
    pitched planned prepared presented preserved prevented prioritized processed procured produced profiled
    programmed projected promoted proposed prototyped proved provided published purchased qualified
    quantified raised ran ranked rated realigned rearchitected rebuilt received recommended reconciled
    recruited rectified redesigned reduced reengineered refactored refined regulated rehabilitated
    reinforced reorganized repaired replaced reported represented researched reshaped resolved restored
    restructured retained revamped reversed reviewed revised revitalized revolutionized saved scaled
    scheduled scoped screened scripted secured segmented selected served set shaped shipped showcased
    simplified simulated slashed sold solved sourced spearheaded specified sped sponsored stabilized
    standardized steered stimulated strategized streamlined strengthened structured studied submitted
    succeeded summarized supervised supported surpassed surveyed sustained synchronized synthesized
    systematized tackled targeted taught tested tracked trained transformed transitioned translated
    tripled troubleshot tuned uncovered underwrote unified united unlocked updated upgraded upheld utilized
    validated verified visualized volunteered won wrote
    """.split()
)

WEAK_STARTS = (
    "responsible for", "worked on", "worked with", "helped", "assisted", "involved in", "participated in",
    "tasked with", "duties included", "in charge of", "handled", "did ", "was ", "were ", "i ", "my ",
    "team player", "various", "etc",
)

# Present-tense irregulars that map to past-tense verbs above.
IRREGULAR_PRESENT = {
    "build": "built", "lead": "led", "drive": "drove", "grow": "grew", "write": "wrote", "win": "won",
    "cut": "cut", "oversee": "oversaw", "run": "ran", "teach": "taught", "sell": "sold", "make": "made",
    "set": "set", "speed": "sped", "own": "owned",
}

# --- Resume section headings -----------------------------------------------------------------
SECTION_ALIASES: dict[str, list[str]] = {
    "summary": [
        "summary", "professional summary", "profile", "professional profile", "objective", "career objective",
        "about me", "about", "overview", "career summary", "executive summary", "summary of qualifications",
        "personal statement", "introduction",
    ],
    "experience": [
        "experience", "work experience", "professional experience", "employment", "employment history",
        "work history", "career history", "relevant experience", "internships", "internship",
        "internship experience", "experience and internships", "professional background", "industry experience",
    ],
    "projects": [
        "projects", "personal projects", "academic projects", "key projects", "selected projects",
        "side projects", "project experience", "technical projects", "notable projects", "open source",
    ],
    "skills": [
        "skills", "technical skills", "core skills", "key skills", "skills and tools", "technologies",
        "tech stack", "core competencies", "competencies", "tools", "technical proficiencies",
        "areas of expertise", "expertise", "skills summary", "technical expertise", "skills and interests",
        "languages and tools", "technical summary", "skill set", "skillset", "tools and technologies",
    ],
    "education": [
        "education", "academic background", "academics", "education and certifications", "qualifications",
        "academic qualifications", "educational background", "education and training",
    ],
    "certifications": [
        "certifications", "certificates", "licenses and certifications", "licenses", "courses", "coursework",
        "training", "relevant coursework", "certifications and training", "professional development",
    ],
    "achievements": [
        "achievements", "awards", "honors", "honours", "honors and awards", "accomplishments",
        "awards and achievements", "publications", "leadership", "volunteer", "volunteering",
        "volunteer experience", "extracurricular", "extracurricular activities", "activities",
        "leadership and activities", "positions of responsibility", "interests", "hobbies", "languages",
        "research", "research experience", "patents", "talks", "references",
    ],
}

HEADING_LOOKUP: dict[str, str] = {}
for _kind, _names in SECTION_ALIASES.items():
    for _name in _names:
        HEADING_LOOKUP[_name] = _kind

STOPWORDS = set(
    """
    a an and are as at be by for from has have in into is it its of on or that the their this to was were will
    with you your we our they them who what when where which while within without across over under per via
    using use used etc including such other more most very well also able strong excellent good great new
    """.split()
)

TITLE_NOISE = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "head", "chief", "associate", "assistant",
    "intern", "trainee", "i", "ii", "iii", "iv", "1", "2", "3", "level", "mid", "entry", "remote", "hybrid",
    "onsite", "full-time", "part-time", "contract", "of", "and", "the", "-", "/", "&",
}

# AI Data Steward

AI Data Steward is an **AI-assisted data stewardship platform** that combines metadata-driven ingestion, automated data quality, AI-generated business rules, remediation workflows, and human-in-the-loop governance.

Unlike traditional data quality tools that begin after data has been loaded, AI Data Steward manages the complete stewardship lifecycle—from dataset registration through curated data creation—with full load history and row-level lineage.

---

## Key Features

### Metadata-Driven Data Management
- Metadata-driven dataset registration
- CSV and fixed-width dataset support
- Configurable parser definitions
- Incremental loading (Upsert, Append, Replace)
- Centralized configuration (`config.py` + `.env`)

### Data Quality
- Automatic data profiling
- AI-generated business definitions
- AI-generated data quality rules
- Rule guardrails and validation
- Human approval workflow
- Rule evaluation engine
- Failed record identification

### Data Stewardship
- AI-generated remediation suggestions
- Human approval workflow for remediations
- Curated dataset generation
- Dataset 360 workspace
- Steward Workbench

### Governance & Lineage
- Load history tracking
- Row-level file lineage
- Dataset health monitoring
- Rule Catalog
- Failed Records Explorer
- Remediation Queue

### User Experience
- Streamlit web application
- Metadata-driven workflows
- Incremental Data Load page
- Dataset dashboards
- Interactive stewardship experience

---

# Stewardship Workflow

```
                Register Dataset
                       │
                       ▼
            Incremental Data Load
                       │
                       ▼
                Load History
                       │
                       ▼
                  Raw Dataset
                       │
                       ▼
                Data Profiling
                       │
                       ▼
          AI Rule Generation (LLM)
                       │
                       ▼
              Human Rule Approval
                       │
                       ▼
              Rule Evaluation
                       │
                       ▼
      AI Remediation Suggestions
                       │
                       ▼
        Human Remediation Approval
                       │
                       ▼
               Curated Dataset
                       │
                       ▼
             Dataset Health Monitor
```

---

# Platform Architecture

```
                +----------------------+
                |  Metadata Repository |
                +----------+-----------+
                           |
          +----------------+----------------+
          |                                 |
          ▼                                 ▼
 Dataset Registration              Parser Definitions
          |                                 |
          +----------------+----------------+
                           |
                           ▼
                 Incremental Loader
                           |
                           ▼
                    Raw Data Layer
                           |
                           ▼
                  Data Profiling Engine
                           |
                           ▼
                  AI Rule Generator
                           |
                           ▼
                  Rule Evaluation Engine
                           |
                           ▼
               Remediation Generator
                           |
                           ▼
                   Curated Data Layer
                           |
                           ▼
                Streamlit User Interface
```

---

# Current Capabilities

- ✅ Metadata-driven ingestion
- ✅ Dataset registration
- ✅ Fixed-width parsing
- ✅ CSV parsing
- ✅ Incremental loading
- ✅ Upsert / Append / Replace loading
- ✅ Automatic profiling
- ✅ AI-generated quality rules
- ✅ Human rule approval
- ✅ Rule evaluation
- ✅ Failed record capture
- ✅ AI-generated remediation suggestions
- ✅ Human remediation approval
- ✅ Curated data creation
- ✅ Dataset Health dashboard
- ✅ Load history
- ✅ Row-level lineage
- ✅ Streamlit management interface

---

# Technology Stack

| Layer | Technology |
|--------|------------|
| Language | Python |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| AI | Ollama (Llama) |
| UI | Streamlit |
| Data Processing | Pandas |
| Version Control | Git |

---

# Project Structure

```
AIDataSteward/
│
├── config.py
├── streamlit_app.py
├── requirements.txt
├── README.md
├── CHANGELOG.md
│
├── scripts/
│   ├── load_dataset.py
│   ├── profile_dataset.py
│   ├── evaluate_rules.py
│   ├── generate_rules.py
│   ├── generate_remediation_suggestions.py
│   ├── apply_remediations.py
│   └── run_pipeline.py
│
├── sql/
│
├── docs/
│
├── raw_data/
│
└── curated/
```

---

# Running the Application

Clone the repository:

```bash
git clone <repository-url>
cd AIDataSteward
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure environment variables:

```text
DB_HOST=
DB_NAME=
DB_USER=
DB_PASSWORD=

OLLAMA_HOST=
OLLAMA_MODEL=
```

Launch the application:

```bash
streamlit run streamlit_app.py
```

Register a dataset and execute the complete stewardship workflow directly through the Streamlit interface.

---

# Roadmap

## Version 1.1.0 (Current)

- Metadata-driven ingestion
- Incremental loading
- AI-generated quality rules
- Human stewardship workflows
- Dataset Health dashboard
- Load history
- Row-level lineage

## Version 1.2 (Planned)

- Cross-column business rules
- Dataset-level quality rules
- Reference data validation
- Enhanced quality scoring
- Rule architecture improvements

## Future Vision

- AI Steward Copilot
- Enterprise lineage graph
- REST API
- Scheduled stewardship workflows
- Multi-user security
- Semantic metadata layer

---

# License

This project is currently provided for educational, research, and demonstration purposes.

---

# About

AI Data Steward explores how **Artificial Intelligence can augment enterprise data stewardship** by combining metadata management, automated data quality, human governance, and explainable AI into a single platform.

# AI Data Steward

AI Data Steward is an AI-assisted data governance platform that automates data profiling, data quality rule generation, rule evaluation, remediation suggestions, and human-in-the-loop data stewardship.

## Features

- Metadata-driven dataset registration
- Fixed-width and CSV dataset support
- Automatic data profiling
- LLM-generated business definitions and quality rules
- Rule guardrails
- Human approval workflow
- Data quality evaluation
- Remediation suggestion generation
- Curated data creation
- Streamlit web interface
- Dataset 360 dashboard
- Steward Workbench

## Architecture

```
Register Dataset
        │
        ▼
Load Dataset
        │
        ▼
Profile Dataset
        │
        ▼
LLM Rule Generation
        │
        ▼
Rule Approval
        │
        ▼
Rule Evaluation
        │
        ▼
Remediation Suggestions
        │
        ▼
Remediation Approval
        │
        ▼
Curated Dataset
```

## Technology

- Python
- PostgreSQL
- SQLAlchemy
- Streamlit
- Ollama (Llama)
- Pandas

## Project Structure

```
scripts/
streamlit_app.py
metadata/
docs/
sql/
```

## Running

Start the Streamlit application:

```bash
streamlit run streamlit_app.py
```

Register a dataset and execute the entire stewardship workflow directly from the application.

## Status

Current Version: **1.0.0**

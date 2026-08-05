# Changelog

## v1.0.0

Initial release

    ### Features

    - Generic metadata-driven loader
    - Generic profiling engine
    - LLM-generated data quality rules
    - Rule guardrails
    - Rule evaluation engine
    - Remediation suggestion engine
    - Curated data layer
    - Steward Workbench
    - Dataset 360
    - Dataset registration
    - Parser registration
    - Streamlit operations console


## v1.1.0

    ### Added
    - Incremental Data Load page
    - Metadata-driven upsert loading
    - Append and Replace load modes
    - Load history tracking
    - Row-level file lineage
    - Dataset health banner
    - Centralized configuration
    - SQL environment initialization

    ### Improved
    - Rule approval workflow
    - Streamlit navigation
    - Dataset registration
    - Parser management
    - Git project structure

    ### Fixed
    - Unsupported rule execution handling
    - Incremental validation
    - Fixed-width parser improvements
    - Configuration management
    
### Configurable LLM Provider

AI Data Steward uses a provider abstraction for LLM interactions.

Currently validated:
- Ollama local models

Implemented but not yet validated:
- OpenAI API provider
- Anthropic API provider

The active provider and model are selected through environment variables.

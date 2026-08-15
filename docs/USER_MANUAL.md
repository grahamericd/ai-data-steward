# AI Steward User Manual

This manual explains how to configure, operate, and govern data with AI Steward. It is intended for data stewards, data owners, analysts, and platform administrators.

## Contents

1. [What AI Steward does](#what-ai-steward-does)
2. [Core concepts](#core-concepts)
3. [Getting started](#getting-started)
4. [Recommended stewardship workflow](#recommended-stewardship-workflow)
5. [Using the application](#using-the-application)
6. [Rules and evaluation](#rules-and-evaluation)
7. [Remediation and curated data](#remediation-and-curated-data)
8. [Lineage, runs, and audit history](#lineage-runs-and-audit-history)
9. [Reference data](#reference-data)
10. [Command-line operation](#command-line-operation)
11. [Configuration](#configuration)
12. [Troubleshooting](#troubleshooting)
13. [Governance guidance](#governance-guidance)

## What AI Steward does

AI Steward manages a governed data-quality workflow from source ingestion through curated output:

```text
Dataset registration
    ↓
Source load and raw-row lineage
    ↓
SQL-native profiling
    ↓
Rule generation and deterministic guardrails
    ↓
Human rule approval
    ↓
Isolated rule evaluation
    ↓
Failed-record capture
    ↓
Safe remediation suggestions
    ↓
Human remediation approval
    ↓
Immutable curated version and change history
```

The language model proposes and explains rules where appropriate. It does not bypass rule-registry validation, approve its own rules, or apply remediations without steward approval.

## Core concepts

### Registered dataset

A dataset registration tells the platform how to identify, parse, and store a source. It includes a dataset name, source type, raw schema and table, source file, and optional primary key.

### Raw data

Loaded source records are stored in the registered raw table. The loader adds technical lineage fields so records can be associated with their source file and load run.

### Column profile

A profile contains observed metadata such as row count, null count, distinct count, minimum, maximum, inferred type, and small sample values. Basic statistics are calculated in PostgreSQL rather than by loading an entire large table into application memory.

### Rule

A rule is a versioned JSON contract in `dq.rule`. Its `executable_rule` contains a registry-supported rule type and a standardized `parameters` object. Rules can have column, row, or dataset scope.

### Result and failed record

Each rule evaluation creates a `dq.result`. Individual failing rows are stored in `dq.failed_record`, connecting a source-row identifier to the result and rule.

### Remediation

A remediation is a proposed correction for a specific failed record. It retains its originating rule, result, and failed-record identifiers. Suggestions may be deterministic or LLM-assisted. The system does not create a suggestion when it cannot propose a safe correction.

### Curated version

Applying approved remediations creates an immutable physical snapshot in the `curated` schema. Each version records its predecessor, producing remediation run, raw-row lineage, and field-level changes. A stable `<raw_table>__current` view points to the latest completed version.

### Stewardship run

A stewardship run groups load, profiling, rule generation, evaluation, and remediation activity for one dataset. Each phase records its actor, status, start and completion time, and errors.

## Getting started

### Prerequisites

- Python and the project virtual environment
- PostgreSQL
- An Ollama installation and model, or an OpenAI API key
- Database credentials with permission to use the application schemas

### Install dependencies

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure the environment

Copy `.env.example` to `.env` and set at least:

```dotenv
DB_USER=your_database_user
DB_PASSWORD=your_database_password
DB_HOST=localhost
DB_NAME=your_database_name

LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
LLM_TIMEOUT_SECONDS=120
OLLAMA_HOST=http://localhost:11434
```

For OpenAI, use:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=your_configured_model
OPENAI_API_KEY=your_api_key
```

Prompt content is sent to the configured external provider when `LLM_PROVIDER=openai`.

### Prepare the database

For a new installation, run `sql/001_bootstrap_database.sql`. For an existing installation, apply the numbered migrations in order through `sql/010_curated_versioning.sql`. Use your organization’s normal migration process and take a database backup before applying migrations.

### Start the application

```bash
.venv/bin/streamlit run streamlit_app.py
```

If a recently changed Python module still appears stale, stop Streamlit completely with `Ctrl+C` and restart it.

## Recommended stewardship workflow

### 1. Enter a steward identity

Enter your name or organizational identity in the sidebar. It is required for approvals, rejections, full pipeline runs, and applying remediations. Add a decision note whenever context will help a future reviewer.

### 2. Register the dataset

Use **Register Dataset** to define the source and raw destination. Configure a stable primary key whenever possible; upserts, failed-row identification, remediation, and curated-row lineage depend on it.

### 3. Load source data

Use **Incremental Data Load** to upload, preview, and load a source file. Confirm the detected columns and selected load mode before loading.

### 4. Profile and generate rules

Use **Run Pipeline**. You may run individual rule-generation families or the full workflow. Generated rules remain proposed until a steward approves them.

### 5. Review rules

Use **Steward Workbench** to inspect the rule definition, empirical evidence, provider, model, prompt version, and generation timestamp. Approve only rules that represent an actual reusable quality expectation.

### 6. Evaluate approved rules

Return to **Run Pipeline** and select **Evaluate Approved Rules**. A malformed or unsupported rule receives `ERROR`; other rules continue evaluating.

### 7. Investigate failures

Use **Failed Records** and **Dataset 360** to identify the rule, result, source-row identifier, source load, and recorded details.

### 8. Review remediation suggestions

Generate suggestions from **Run Pipeline**, then approve or reject them in **Remediation Queue**. Compare original and suggested values and verify the originating failure.

### 9. Apply approved remediations

Use **Apply Approved Remediations**. The platform creates a new immutable curated version; it does not overwrite the preceding curated state.

## Using the application

### Dashboard

The Dashboard provides a portfolio summary:

- loaded datasets
- total and approved rules
- open remediation work
- pass, fail, and error counts by dataset
- failed-record totals
- recent evaluation results

Use it to identify datasets that need investigation. An `ERROR` is different from a failed business rule: it means the evaluator could not produce a trustworthy result.

### Register Dataset

Complete the registration form as follows:

| Field | Purpose |
|---|---|
| Dataset Name | Stable internal identifier; use letters, numbers, and underscores |
| Display Name | Human-readable name |
| Description | Business purpose and source context |
| Source Type | `csv` or `fixed_width` |
| Parser Name | Parser label used by the registration |
| Source File | Default file used by command-line/full-pipeline loading |
| Raw Schema | PostgreSQL destination schema, normally `raw` |
| Raw Table | PostgreSQL destination table |
| Primary Key | Stable source-row identifier |
| Active | Whether the dataset is available to workflows |

Submitting an existing dataset name updates its registration.

#### Fixed-width parser definitions

For a fixed-width source, select the registered dataset and paste a CSV-formatted parser definition:

```csv
column_name,start_position,field_length,sequence_number
document_number,1,12,1
business_name,13,80,2
state_code,93,2,3
```

- Positions are one-based.
- Sequence numbers control output-column order.
- Choose whether to replace the existing definition.
- Preview an uploaded file before loading to verify field boundaries.

### Incremental Data Load

Select a dataset, confirm its configuration, choose a load mode, and upload a file.

| Mode | Behavior | Important consideration |
|---|---|---|
| Upsert | Inserts new primary keys and updates matching keys | Requires a registered primary key |
| Append | Adds every incoming record | Can create duplicates unless separately governed |
| Replace | Rebuilds the raw table from the incoming source | Destructive to the current raw table; curated versions remain separate |

The page displays a parsed preview, row and column counts, load results, recent load history, actor, duration, and error details. Uploaded temporary files are removed after processing.

### Dataset 360

Dataset 360 is the main investigation workspace for one dataset.

#### Dataset health

The health banner uses the latest result for each approved rule:

| State | Meaning |
|---|---|
| Not Loaded | No successful load run exists |
| Not Yet Assessed | Data is loaded but approved rules have not been evaluated |
| Evaluation Error | One or more rule evaluations could not complete reliably |
| Healthy | Quality score is at least 95% |
| Needs Attention | Quality score is at least 85% but below 95% |
| At Risk | Quality score is below 85% |

The quality score is the percentage of evaluated approved rules whose latest result is `PASS`. Review errors separately before trusting the score.

#### Stewardship run history

Select a run to inspect each phase and its status, actor, timing, load-run link, error, and artifact counts. This answers, “What happened to this dataset during this run?”

#### Dataset tabs

- **Overview:** registration and source details.
- **Columns:** inferred type, nulls, distinct values, range, and samples.
- **Rules:** definitions, scope, target columns, and status.
- **Quality:** result history, score, failures, and result details.
- **Remediation:** remediation counts and suggestion details.
- **Raw vs Curated:** immutable version history, raw-versus-curated comparison, raw load lineage, and changes linked to rules and failures.

### Steward Workbench

The Workbench is the primary rule-review inbox. It shows proposed rules, guardrail rejections, proposed remediations, and failed results.

For a selected rule, review:

- dataset and target columns
- business definition and executable parameters
- empirical evidence
- LLM provider and model
- prompt version and generated time
- existing decision details

**Approve Rule** first validates and canonicalizes the rule against the registry. Unsupported or malformed rules cannot be approved. **Reject Rule** records the steward, timestamp, and optional note.

### Rule Catalog

The catalog displays all rules and their provenance. Select a rule ID and status to propose, approve, reject, or retire it. Every transition is written to rule audit history.

Retire obsolete rules instead of deleting them so historical results remain explainable.

### Failed Records

The Failed Records page reads from `dq.failed_record`, not display samples. Select a result to see:

- failed-record ID
- result and rule IDs
- dataset and rule type
- source-row identifier
- evaluation and failure timestamps

Use the IDs to trace a failure through evaluation, remediation, and curated history.

### Remediation Queue

Filter suggestions by `proposed`, `approved`, `rejected`, `applied`, or all. A selected suggestion shows:

- originating rule and result
- failed-record and source-row identifiers
- deterministic or LLM-assisted generation method
- original and suggested values
- confidence score
- complete decision history

Approval does not immediately change data. Approved suggestions are applied later from **Run Pipeline**, where they produce a new curated version.

### Run Pipeline

The page supports both individual activities and a coordinated full run.

#### Rule generation

- **Generate Column Rules:** proposes single-column rules.
- **Generate Multi-Column Rules:** empirically screens date, status, identifier, conditional-completeness, and repeated-structure relationships before semantic review.
- **Generate Dataset Rules:** proposes whole-dataset constraints.
- Reference-rule generation is included in the full pipeline.

Local Ollama analysis is intentionally constrained and serial. API-backed providers can review more empirically supported multi-column candidates with bounded concurrency.

#### Full pipeline

The full pipeline performs:

1. load
2. profiling
3. column, multi-column, dataset, and reference rule generation
4. approved-rule evaluation
5. remediation suggestion generation

Each phase is recorded under a stewardship run. A failed phase stops the run and records its error. Rule approval and remediation approval remain human decisions outside the automated sequence.

#### Individual operations

Use the remaining controls to evaluate approved rules, generate remediation suggestions, or apply approved remediations independently.

### Raw Data Preview

Select a dataset to display up to 100 raw records. This page is intended for quick inspection, not bulk export or editing.

## Rules and evaluation

### Supported column rules

- `allowed_values`
- `not_null`
- `max_length`
- `min_length`
- `regex`
- `numeric_range`
- `percentage_range`
- `date_format`
- `city_contains_state_or_zip`
- `state_field_contains_zip`
- `reference_value`

### Supported row rules

- `column_comparison`
- `conditional_required`
- `at_least_one_present`
- `columns_equal`
- `reference_combination`
- `city_state_zip_reference`

### Supported dataset rules

- `minimum_row_count`
- `primary_key_unique`
- `column_combination_unique`

### Result statuses

| Status | Meaning |
|---|---|
| PASS | The evaluated records met the rule |
| FAIL | One or more records violated the rule |
| SKIPPED | The rule was intentionally not evaluated |
| ERROR | The rule was malformed, unsupported, or failed during evaluation |

Rules are evaluated independently. One error does not roll back previously stored results or prevent remaining rules from running. The exception or validation problem is recorded in `dq.result.details`.

## Remediation and curated data

### Safe-remediation policy

- Suggestions originate from relational failed records.
- Every suggestion identifies its rule and result.
- Deterministic remediation is preferred when the correction is unambiguous.
- LLM-assisted remediation is labeled separately.
- No suggestion is generated when the platform cannot safely determine a correction.
- A steward must approve a suggestion before it can be applied.

### Curated version lineage

```text
Rule
  ↓
Result
  ↓
Failed record and raw source row
  ↓
Remediation suggestion and steward decision
  ↓
Remediation run
  ↓
Curated version and field-level change history
```

Each application run creates a new physical table such as `customers__v42`. Previous versions remain available. `curated.change_history` records old and new field values and their originating remediation, rule, result, and failed record.

## Lineage, runs, and audit history

The platform provides several complementary histories:

- **Load history:** source file, mode, counts, actor, duration, and error.
- **Raw-row lineage:** load run and source file attached to loaded rows.
- **Stewardship run history:** phase-by-phase workflow activity.
- **Rule audit:** every rule status transition, actor, timestamp, and note.
- **Remediation audit:** every remediation status transition, actor, timestamp, and note.
- **Failure lineage:** failed row to result and rule.
- **Curated lineage:** curated row and field change back to the raw source and approved correction.

Do not use placeholder identities for governed actions. Use an identity that your organization can associate with a person or service account.

## Reference data

AI Steward supports registered authoritative datasets for states, ZIP codes, places, counties, FIPS codes, and NAICS codes. Reference rules validate facts deterministically rather than asking an LLM to guess geographic or classification relationships.

An administrator can load a registered reference CSV with:

```bash
.venv/bin/python scripts/load_reference_dataset.py \
  us_states /path/to/us_states.csv \
  --replace \
  --source-version 2026
```

Use `--replace` only when the incoming file is a complete authoritative replacement. Record a meaningful source version. The CSV columns must match the registered reference table.

Generate deterministic reference-rule proposals with:

```bash
.venv/bin/python scripts/generate_reference_rules.py <dataset_name>
```

Generated reference rules are still proposed rules and require steward approval.

## Command-line operation

Run commands from the repository root with the configured virtual environment.

```bash
# Load the registered source file
.venv/bin/python scripts/load_dataset.py <dataset_name>

# Profile a dataset
.venv/bin/python scripts/profile_dataset.py <dataset_name>

# Generate rule families
.venv/bin/python scripts/generate_rules.py <dataset_name>
.venv/bin/python scripts/generate_multicolumn_rules.py <dataset_name>
.venv/bin/python scripts/generate_dataset_rules.py <dataset_name>
.venv/bin/python scripts/generate_reference_rules.py <dataset_name>

# Evaluate approved rules
.venv/bin/python scripts/evaluate_rules.py <dataset_name>

# Generate and apply remediation
.venv/bin/python scripts/generate_remediation_suggestions.py <dataset_name>
.venv/bin/python scripts/apply_remediations.py <dataset_name> --actor <identity>

# Run the coordinated workflow
.venv/bin/python scripts/run_pipeline.py <dataset_name> --actor <identity>

# Run automated tests
.venv/bin/pytest -q
```

## Configuration

### General environment variables

| Variable | Purpose |
|---|---|
| `DB_USER` | PostgreSQL user |
| `DB_PASSWORD` | PostgreSQL password |
| `DB_HOST` | PostgreSQL host |
| `DB_NAME` | PostgreSQL database |
| `RAW_DATA_DIR` | Default source-data directory |
| `UPLOAD_DIR` | Upload directory when used by supporting workflows |
| `STAGING_SCHEMA` | Staging schema name |
| `LLM_PROVIDER` | `ollama` or `openai` |
| `LLM_MODEL` | Provider model name |
| `LLM_TIMEOUT_SECONDS` | Provider timeout |
| `OLLAMA_HOST` | Ollama HTTP endpoint |
| `OPENAI_API_KEY` | OpenAI credential when using OpenAI |

### Multi-column analysis controls

| Variable | Default | Purpose |
|---|---:|---|
| `MULTICOLUMN_SAMPLE_ROWS` | 10000 | Maximum sampled rows for empirical analysis |
| `MULTICOLUMN_OLLAMA_MAX_CANDIDATES` | 3 | Local candidates sent to Ollama |
| `MULTICOLUMN_API_MAX_CANDIDATES` | 12 | Candidates sent to an API provider |
| `MULTICOLUMN_API_MAX_WORKERS` | 4 | Maximum concurrent API reviews |

Keep `.env` out of source control. Commit `.env.example` with placeholders only.

## Troubleshooting

### Streamlit reports an import that should exist

Streamlit may retain an earlier imported module after files change. Stop it completely and restart:

```bash
Ctrl+C
.venv/bin/streamlit run streamlit_app.py
```

### Database connection fails

- Confirm PostgreSQL is running and reachable.
- Check `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`.
- Confirm the user can access the `metadata`, `dq`, `raw`, `curated`, `reference`, and `staging` schemas.
- Confirm all required migrations have been applied.

### No datasets appear

Register an active dataset. If it already exists, confirm `metadata.dataset.active` is true.

### Upsert is unavailable

Configure a primary key for the dataset. Use append only if duplicates are acceptable and governed separately.

### Fixed-width preview is incorrect

Verify one-based start positions, field lengths, and sequence numbers. Replace the parser definition and preview the file again before loading.

### No rules are generated

- Run profiling first.
- Confirm the LLM provider and model are available.
- Review script output for provider or validation errors.
- Multi-column rules require sufficient empirical support; no proposal is preferable to a speculative rule.

### A rule cannot be approved

The rule failed central registry validation or uses an unsupported type, scope, operator, or parameter structure. Review its definition and generation output. Do not bypass the approval guardrail.

### A rule result is ERROR

Inspect `dq.result.details` and the Rule Catalog. Correct or retire the broken rule, then evaluate again. Other rules from the same run should still have results.

### No remediation was generated

The failure may not have a safe deterministic or LLM-assisted correction. Confirm that the dataset has a primary key and that relational failed records exist. Manual source correction may be appropriate.

### No curated version exists

Approve at least one safe remediation and run **Apply Approved Remediations** with a steward identity. The first application creates the initial immutable snapshot even if no earlier curated version exists.

## Governance guidance

- Treat generated rules as proposals, not facts.
- Use authoritative reference data for factual relationships.
- Require meaningful steward identities and decision notes.
- Investigate evaluation errors separately from data failures.
- Never approve a remediation without checking its originating rule and source row.
- Prefer retiring rules to deleting them.
- Preserve migrations, audit tables, failed records, and curated versions according to organizational retention policy.
- Restrict database permissions and external LLM use according to data-classification requirements.
- Back up PostgreSQL before schema changes or destructive raw-data replacement loads.

The planned AI Steward Copilot is not part of the current interface. When introduced, it should initially remain read-only and provide evidence-backed explanations over the trusted metadata, run, lineage, rule, result, failure, remediation, and audit records described here.

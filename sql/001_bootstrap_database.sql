-- AI Data Steward database bootstrap
-- Creates the core schemas, tables, constraints, and indexes.
-- Run against an existing empty PostgreSQL database.

BEGIN;

CREATE SCHEMA IF NOT EXISTS metadata;
CREATE SCHEMA IF NOT EXISTS dq;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS reference;
CREATE SCHEMA IF NOT EXISTS staging;

-- =========================================================
-- Metadata registry
-- =========================================================

CREATE TABLE IF NOT EXISTS metadata.dataset (
    dataset_id      SERIAL PRIMARY KEY,
    dataset_name    TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    description     TEXT,
    source_type     TEXT NOT NULL,
    parser_name     TEXT,
    source_file     TEXT NOT NULL,
    raw_schema      TEXT NOT NULL DEFAULT 'raw',
    raw_table       TEXT NOT NULL,
    primary_key     TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT dataset_source_type_check
        CHECK (source_type IN ('csv', 'fixed_width', 'json', 'xml', 'api'))
);

CREATE TABLE IF NOT EXISTS metadata.parser_definition (
    parser_id       SERIAL PRIMARY KEY,
    dataset_id      INTEGER NOT NULL
                    REFERENCES metadata.dataset(dataset_id)
                    ON DELETE CASCADE,
    column_name     TEXT NOT NULL,
    start_position  INTEGER NOT NULL,
    field_length    INTEGER NOT NULL,
    sequence_number INTEGER NOT NULL,

    CONSTRAINT parser_start_position_check
        CHECK (start_position > 0),

    CONSTRAINT parser_field_length_check
        CHECK (field_length > 0),

    CONSTRAINT parser_sequence_check
        CHECK (sequence_number > 0),

    CONSTRAINT parser_dataset_column_unique
        UNIQUE (dataset_id, column_name),

    CONSTRAINT parser_dataset_sequence_unique
        UNIQUE (dataset_id, sequence_number)
);

CREATE TABLE IF NOT EXISTS metadata.column_profile (
    profile_id      SERIAL PRIMARY KEY,
    dataset_name    TEXT NOT NULL,
    column_name     TEXT NOT NULL,
    row_count       BIGINT,
    null_count      BIGINT,
    null_percent    NUMERIC,
    distinct_count  BIGINT,
    inferred_type   TEXT,
    min_value       TEXT,
    max_value       TEXT,
    sample_values   JSONB,
    profiled_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT column_profile_unique
        UNIQUE (dataset_name, column_name)
);

CREATE TABLE IF NOT EXISTS metadata.reference_dataset (
    reference_dataset_name TEXT PRIMARY KEY,
    display_name           TEXT NOT NULL,
    schema_name            TEXT NOT NULL DEFAULT 'reference',
    table_name             TEXT NOT NULL,
    authority_name         TEXT NOT NULL,
    source_url             TEXT,
    source_version         TEXT,
    key_columns            JSONB NOT NULL,
    refreshed_at           TIMESTAMPTZ,
    active                 BOOLEAN NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS metadata.dataset_run (
    run_id                  UUID PRIMARY KEY,
    dataset_id              INTEGER NOT NULL
                            REFERENCES metadata.dataset(dataset_id)
                            ON DELETE CASCADE,
    started_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at            TIMESTAMP,
    rows_loaded             BIGINT,
    profile_complete        BOOLEAN NOT NULL DEFAULT FALSE,
    rules_generated         BOOLEAN NOT NULL DEFAULT FALSE,
    evaluation_complete     BOOLEAN NOT NULL DEFAULT FALSE,
    remediation_complete    BOOLEAN NOT NULL DEFAULT FALSE,
    run_status              TEXT NOT NULL DEFAULT 'running',
    error_message           TEXT
);

CREATE TABLE IF NOT EXISTS metadata.load_run (
    load_run_id       BIGSERIAL PRIMARY KEY,
    dataset_id        INTEGER REFERENCES metadata.dataset(dataset_id),
    dataset_name      TEXT NOT NULL,
    source_file       TEXT,
    source_type       TEXT,
    load_mode         TEXT NOT NULL,
    target_schema     TEXT,
    target_table      TEXT,
    primary_key_name  TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    rows_received     INTEGER DEFAULT 0,
    rows_inserted     INTEGER DEFAULT 0,
    rows_updated      INTEGER DEFAULT 0,
    rows_rejected     INTEGER DEFAULT 0,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ,
    duration_seconds  NUMERIC,
    initiated_by      TEXT,
    error_message     TEXT,
    details           JSONB
);

CREATE TABLE IF NOT EXISTS metadata.stewardship_run (
    stewardship_run_id BIGSERIAL PRIMARY KEY,
    dataset_id         INTEGER NOT NULL
                       REFERENCES metadata.dataset(dataset_id),
    dataset_name       TEXT NOT NULL,
    initiated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    initiated_by       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'running',
    error_message      TEXT,
    CONSTRAINT stewardship_run_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS metadata.stewardship_run_phase (
    phase_id           BIGSERIAL PRIMARY KEY,
    stewardship_run_id BIGINT NOT NULL
                       REFERENCES metadata.stewardship_run(stewardship_run_id)
                       ON DELETE CASCADE,
    phase_name         TEXT NOT NULL,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    actor              TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'running',
    load_run_id        BIGINT REFERENCES metadata.load_run(load_run_id),
    error_message      TEXT,
    CONSTRAINT stewardship_phase_name_check
        CHECK (phase_name IN ('load', 'profiling', 'rule_generation', 'evaluation', 'remediation')),
    CONSTRAINT stewardship_phase_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

ALTER TABLE metadata.load_run
ADD COLUMN IF NOT EXISTS stewardship_run_id BIGINT
    REFERENCES metadata.stewardship_run(stewardship_run_id);
-- =========================================================
-- Data-quality rule repository
-- =========================================================

CREATE TABLE IF NOT EXISTS dq.rule (
    id                SERIAL PRIMARY KEY,
    dataset_name      TEXT NOT NULL,
    column_name       TEXT,
    rule_type         TEXT NOT NULL,
    rule_definition   JSONB NOT NULL,
    status            TEXT NOT NULL DEFAULT 'proposed',
    confidence_score  NUMERIC,
    generated_by      TEXT,
    llm_provider      TEXT,
    llm_model         TEXT,
    prompt_version    TEXT,
    stewardship_run_id BIGINT REFERENCES metadata.stewardship_run(stewardship_run_id),
    decision_by       TEXT,
    decision_at       TIMESTAMP,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT rule_status_check
        CHECK (
            status IN (
                'proposed',
                'approved',
                'rejected',
                'guardrail_rejected',
                'retired'
            )
        )
);

CREATE TABLE IF NOT EXISTS dq.rule_audit (
    id                BIGSERIAL PRIMARY KEY,
    rule_id           INTEGER NOT NULL
                      REFERENCES dq.rule(id) ON DELETE CASCADE,
    previous_status   TEXT NOT NULL,
    new_status        TEXT NOT NULL,
    changed_by        TEXT NOT NULL,
    changed_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decision_note     TEXT
);

CREATE TABLE IF NOT EXISTS dq.result (
    id              SERIAL PRIMARY KEY,
    dataset_name    TEXT NOT NULL,
    rule_id         INTEGER
                    REFERENCES dq.rule(id)
                    ON DELETE SET NULL,
    result_status   TEXT NOT NULL,
    failed_count    BIGINT NOT NULL DEFAULT 0,
    details         JSONB,
    checked_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stewardship_run_id BIGINT REFERENCES metadata.stewardship_run(stewardship_run_id),

    CONSTRAINT result_status_check
        CHECK (result_status IN ('PASS', 'FAIL', 'SKIPPED', 'ERROR'))
);

CREATE TABLE IF NOT EXISTS dq.failed_record (
    id                      BIGSERIAL PRIMARY KEY,
    result_id               INTEGER NOT NULL
                            REFERENCES dq.result(id)
                            ON DELETE CASCADE,
    rule_id                 INTEGER
                            REFERENCES dq.rule(id)
                            ON DELETE SET NULL,
    dataset_name            TEXT NOT NULL,
    source_row_identifier   TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dq.remediation_suggestion (
    id                     SERIAL PRIMARY KEY,
    dataset_name           TEXT NOT NULL,
    source_table           TEXT,
    rule_id                INTEGER NOT NULL
                           REFERENCES dq.rule(id)
                           ON DELETE RESTRICT,
    result_id              INTEGER NOT NULL
                           REFERENCES dq.result(id)
                           ON DELETE CASCADE,
    failed_record_id       BIGINT NOT NULL
                           REFERENCES dq.failed_record(id)
                           ON DELETE CASCADE,
    source_row_identifier  TEXT,
    issue_type             TEXT NOT NULL,
    remediation_type       TEXT DEFAULT 'suggested',
    generation_method      TEXT NOT NULL DEFAULT 'deterministic',
    stewardship_run_id     BIGINT REFERENCES metadata.stewardship_run(stewardship_run_id),
    original_values        JSONB,
    suggested_values       JSONB,
    confidence_score       NUMERIC,
    status                 TEXT NOT NULL DEFAULT 'proposed',
    created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_by            TEXT,
    approved_at            TIMESTAMP,

    CONSTRAINT remediation_status_check
        CHECK (
            status IN (
                'proposed',
                'approved',
                'rejected',
                'applied'
            )
        ),

    CONSTRAINT remediation_type_check
        CHECK (
            remediation_type IN (
                'automatic',
                'suggested',
                'manual_review',
                'external_reference',
                'source_system'
            )
        ),

    CONSTRAINT remediation_generation_method_check
        CHECK (generation_method IN ('deterministic', 'llm_assisted'))
);

CREATE TABLE IF NOT EXISTS dq.remediation_audit (
    id                BIGSERIAL PRIMARY KEY,
    remediation_id    INTEGER NOT NULL
                      REFERENCES dq.remediation_suggestion(id)
                      ON DELETE CASCADE,
    previous_status   TEXT NOT NULL,
    new_status        TEXT NOT NULL,
    changed_by        TEXT NOT NULL,
    changed_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decision_note     TEXT
);

-- =========================================================
-- Reference structures
-- =========================================================

CREATE TABLE IF NOT EXISTS reference.us_state (
    state_code TEXT PRIMARY KEY,
    state_name TEXT NOT NULL,
    state_fips TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS reference.us_county (
    state_code TEXT NOT NULL,
    county_fips TEXT NOT NULL,
    county_name TEXT NOT NULL,
    full_fips TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS reference.us_place (
    state_code TEXT NOT NULL,
    place_fips TEXT NOT NULL,
    place_name TEXT NOT NULL,
    PRIMARY KEY (state_code, place_fips)
);

CREATE TABLE IF NOT EXISTS reference.us_zip_code (
    zip_code TEXT NOT NULL,
    place_name TEXT NOT NULL,
    state_code TEXT NOT NULL,
    county_fips TEXT,
    latitude NUMERIC,
    longitude NUMERIC,
    PRIMARY KEY (zip_code, place_name, state_code)
);

CREATE TABLE IF NOT EXISTS reference.us_fips (
    fips_code TEXT NOT NULL,
    fips_type TEXT NOT NULL,
    name TEXT NOT NULL,
    state_code TEXT,
    PRIMARY KEY (fips_type, fips_code)
);

CREATE TABLE IF NOT EXISTS reference.naics (
    naics_code TEXT NOT NULL,
    title TEXT NOT NULL,
    hierarchy_level INTEGER,
    publication_year INTEGER NOT NULL,
    PRIMARY KEY (publication_year, naics_code)
);

-- =========================================================
-- Immutable curated dataset versions
-- =========================================================

CREATE TABLE IF NOT EXISTS curated.remediation_run (
    remediation_run_id BIGSERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES metadata.dataset(dataset_id),
    dataset_name TEXT NOT NULL,
    stewardship_run_id BIGINT REFERENCES metadata.stewardship_run(stewardship_run_id),
    initiated_by TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    remediation_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    CONSTRAINT curated_remediation_run_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

ALTER TABLE dq.remediation_suggestion
ADD COLUMN IF NOT EXISTS applied_in_remediation_run_id BIGINT
    REFERENCES curated.remediation_run(remediation_run_id);

CREATE TABLE IF NOT EXISTS curated.dataset_version (
    curated_version_id BIGSERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES metadata.dataset(dataset_id),
    dataset_name TEXT NOT NULL,
    remediation_run_id BIGINT NOT NULL
        REFERENCES curated.remediation_run(remediation_run_id),
    stewardship_run_id BIGINT REFERENCES metadata.stewardship_run(stewardship_run_id),
    previous_version_id BIGINT REFERENCES curated.dataset_version(curated_version_id),
    physical_table_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'building',
    row_count BIGINT,
    CONSTRAINT curated_version_status_check
        CHECK (status IN ('building', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS curated.row_lineage (
    curated_version_id BIGINT NOT NULL
        REFERENCES curated.dataset_version(curated_version_id) ON DELETE CASCADE,
    source_row_identifier TEXT NOT NULL,
    raw_load_run_id BIGINT,
    source_file TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (curated_version_id, source_row_identifier)
);

CREATE TABLE IF NOT EXISTS curated.change_history (
    change_id BIGSERIAL PRIMARY KEY,
    curated_version_id BIGINT NOT NULL
        REFERENCES curated.dataset_version(curated_version_id) ON DELETE CASCADE,
    previous_version_id BIGINT REFERENCES curated.dataset_version(curated_version_id),
    remediation_id INTEGER NOT NULL REFERENCES dq.remediation_suggestion(id),
    rule_id INTEGER NOT NULL REFERENCES dq.rule(id),
    result_id INTEGER NOT NULL REFERENCES dq.result(id),
    failed_record_id BIGINT NOT NULL REFERENCES dq.failed_record(id),
    source_row_identifier TEXT NOT NULL,
    column_name TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================
-- Helpful indexes
-- =========================================================

CREATE INDEX IF NOT EXISTS idx_parser_definition_dataset
    ON metadata.parser_definition(dataset_id, sequence_number);

CREATE INDEX IF NOT EXISTS idx_column_profile_dataset
    ON metadata.column_profile(dataset_name);

CREATE INDEX IF NOT EXISTS idx_rule_dataset_status
    ON dq.rule(dataset_name, status);

CREATE INDEX IF NOT EXISTS idx_rule_audit_rule_changed
    ON dq.rule_audit(rule_id, changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_result_dataset_checked
    ON dq.result(dataset_name, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_failed_record_result
    ON dq.failed_record(result_id);

CREATE INDEX IF NOT EXISTS idx_failed_record_rule
    ON dq.failed_record(rule_id);

CREATE INDEX IF NOT EXISTS idx_failed_record_dataset_row
    ON dq.failed_record(dataset_name, source_row_identifier);

CREATE INDEX IF NOT EXISTS idx_remediation_dataset_status
    ON dq.remediation_suggestion(dataset_name, status);

CREATE INDEX IF NOT EXISTS idx_remediation_source_row
    ON dq.remediation_suggestion(
        dataset_name,
        source_row_identifier
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_remediation_failed_record
    ON dq.remediation_suggestion(failed_record_id)
    WHERE failed_record_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_remediation_result
    ON dq.remediation_suggestion(result_id);

CREATE INDEX IF NOT EXISTS idx_remediation_audit_changed
    ON dq.remediation_audit(remediation_id, changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_curated_version_dataset
    ON curated.dataset_version(dataset_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_curated_change_version
    ON curated.change_history(curated_version_id, change_id);
CREATE INDEX IF NOT EXISTS idx_curated_lineage_source
    ON curated.row_lineage(source_row_identifier);

COMMIT;

-- AI Data Steward database bootstrap
-- Creates the core schemas, tables, constraints, and indexes.
-- Run against an existing empty PostgreSQL database.

BEGIN;

CREATE SCHEMA IF NOT EXISTS metadata;
CREATE SCHEMA IF NOT EXISTS dq;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS reference;

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

    CONSTRAINT result_status_check
        CHECK (result_status IN ('PASS', 'FAIL', 'SKIPPED', 'ERROR'))
);

CREATE TABLE IF NOT EXISTS dq.remediation_suggestion (
    id                     SERIAL PRIMARY KEY,
    dataset_name           TEXT NOT NULL,
    source_table           TEXT,
    rule_id                INTEGER
                           REFERENCES dq.rule(id)
                           ON DELETE SET NULL,
    source_row_identifier  TEXT,
    issue_type             TEXT NOT NULL,
    remediation_type       TEXT DEFAULT 'suggested',
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
        )
);

CREATE TABLE IF NOT EXISTS dq.remediation_audit (
    id                SERIAL PRIMARY KEY,
    remediation_id    INTEGER NOT NULL
                      REFERENCES dq.remediation_suggestion(id)
                      ON DELETE CASCADE,
    action_taken      TEXT NOT NULL,
    action_timestamp  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details           JSONB
);

-- =========================================================
-- Reference structures
-- =========================================================

CREATE TABLE IF NOT EXISTS reference.us_place_names (
    state_code  TEXT NOT NULL,
    place_name  TEXT NOT NULL,
    place_fips  TEXT,
    PRIMARY KEY (state_code, place_name)
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

CREATE INDEX IF NOT EXISTS idx_result_dataset_checked
    ON dq.result(dataset_name, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_remediation_dataset_status
    ON dq.remediation_suggestion(dataset_name, status);

CREATE INDEX IF NOT EXISTS idx_remediation_source_row
    ON dq.remediation_suggestion(
        dataset_name,
        source_row_identifier
    );

COMMIT;

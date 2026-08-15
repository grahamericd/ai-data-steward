-- Metadata-registered authoritative reference data framework.

BEGIN;

CREATE SCHEMA IF NOT EXISTS reference;

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

INSERT INTO metadata.reference_dataset
(reference_dataset_name, display_name, table_name, authority_name, source_url, key_columns)
VALUES
('us_states', 'United States and territories', 'us_state', 'U.S. Census Bureau',
 'https://www.census.gov/library/reference/code-lists/ansi.html', '["state_code", "state_name", "state_fips"]'),
('us_counties', 'U.S. counties and county equivalents', 'us_county', 'U.S. Census Bureau',
 'https://www.census.gov/library/reference/code-lists/ansi.html', '["state_code", "county_fips", "full_fips", "county_name"]'),
('us_places', 'U.S. incorporated and census-designated places', 'us_place', 'U.S. Census Bureau',
 'https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html', '["state_code", "place_fips", "place_name"]'),
('us_zip_codes', 'U.S. ZIP and place combinations', 'us_zip_code', 'Authoritative source selected by data steward',
 NULL, '["zip_code", "place_name", "state_code", "county_fips"]'),
('us_fips', 'Federal Information Processing Series codes', 'us_fips', 'U.S. Census Bureau',
 'https://www.census.gov/library/reference/code-lists/ansi.html', '["fips_code", "fips_type", "name", "state_code"]'),
('naics', 'North American Industry Classification System', 'naics', 'U.S. Census Bureau',
 'https://www.census.gov/naics/', '["naics_code", "publication_year"]')
ON CONFLICT (reference_dataset_name) DO UPDATE
SET display_name = EXCLUDED.display_name,
    table_name = EXCLUDED.table_name,
    authority_name = EXCLUDED.authority_name,
    source_url = EXCLUDED.source_url,
    key_columns = EXCLUDED.key_columns;

CREATE INDEX IF NOT EXISTS idx_reference_zip_state_place
    ON reference.us_zip_code(state_code, place_name, zip_code);
CREATE INDEX IF NOT EXISTS idx_reference_place_name_state
    ON reference.us_place(state_code, place_name);
CREATE INDEX IF NOT EXISTS idx_reference_county_state_name
    ON reference.us_county(state_code, county_name);
CREATE INDEX IF NOT EXISTS idx_reference_naics_code
    ON reference.naics(naics_code);

COMMIT;

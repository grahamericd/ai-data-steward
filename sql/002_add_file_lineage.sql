-- Add latest-file lineage fields to every existing raw table.

DO $$
DECLARE
    table_record RECORD;
BEGIN
    FOR table_record IN
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'raw'
          AND table_type = 'BASE TABLE'
    LOOP
        EXECUTE format(
            'ALTER TABLE raw.%I
             ADD COLUMN IF NOT EXISTS "_source_file" TEXT',
            table_record.table_name
        );

        EXECUTE format(
            'ALTER TABLE raw.%I
             ADD COLUMN IF NOT EXISTS "_ingested_at" TIMESTAMPTZ',
            table_record.table_name
        );

        EXECUTE format(
            'ALTER TABLE raw.%I
             ADD COLUMN IF NOT EXISTS "_load_run_id" BIGINT',
            table_record.table_name
        );
    END LOOP;
END
$$;

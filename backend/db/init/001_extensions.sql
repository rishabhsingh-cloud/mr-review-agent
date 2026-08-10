-- Enabled once on first DB init. Idempotent so it's safe to re-run manually.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector
CREATE EXTENSION IF NOT EXISTS vectorscale;   -- pgvectorscale (DiskANN)

-- Sanity: surface versions in the init log.
DO $$
BEGIN
    RAISE NOTICE 'timescaledb: %', (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb');
    RAISE NOTICE 'vector: %',      (SELECT extversion FROM pg_extension WHERE extname = 'vector');
    RAISE NOTICE 'vectorscale: %', (SELECT extversion FROM pg_extension WHERE extname = 'vectorscale');
END $$;

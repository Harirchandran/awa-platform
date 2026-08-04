-- =================================================================
-- Production schema entrypoint
-- =================================================================
-- Run this file with psql from a clean production database. It applies the
-- canonical consolidated schema and then the ordered migrations.
\echo 'Applying AWA canonical production schema'
\set AWA_SKIP_DEMO_SEED 1
\ir ../supabase_complete.sql
\ir ../docker_run_migrations.sql
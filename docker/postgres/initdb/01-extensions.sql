-- Structured tables and vectors share one instance; pgvector has to exist
-- before any migration references it. Creating an extension needs superuser,
-- which is why this is an initdb script and not an Alembic migration.
CREATE EXTENSION IF NOT EXISTS vector;

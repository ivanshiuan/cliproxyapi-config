-- ──────────────────────────────────────────────────────────────────────
-- Required Postgres extensions for the restaurant API.
-- Loaded on first DB initialization by the docker-compose db service.
-- ──────────────────────────────────────────────────────────────────────

-- For UUID functions used as fallback PK defaults (UUIDv7 is generated app-side).
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Case-insensitive text columns (e.g. emails). Optional but commonly useful.
CREATE EXTENSION IF NOT EXISTS "citext";

-- Trigram indexes for fast LIKE / fuzzy search on menu names, customer notes, etc.
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Vector embeddings — reserved for Phase 2+ CRM / recommendation features.
-- Comment out if not yet available in your image.
-- CREATE EXTENSION IF NOT EXISTS "vector";

-- Force default timezone for this DB. PG won't accept current_database() as
-- an identifier here; we read POSTGRES_DB out of the runtime env at the
-- session level via the connection string instead. As a belt-and-braces
-- measure we ensure tomestamps default to TPE.
SET timezone TO 'Asia/Taipei';

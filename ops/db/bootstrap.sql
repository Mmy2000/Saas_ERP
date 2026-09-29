-- One-time setup of the database roles from PROJECT_PLAN.md §5.3. Run as a PostgreSQL superuser:
--
--   psql -U postgres -h localhost -f ops/db/bootstrap.sql -v dbname=SaasDB -v owner_pw=... -v rw_pw=... -v platform_pw=...
--
-- app_owner     owns the tables; runs migrations and the test suite (CREATEDB for the test DB).
--               Not a superuser, so FORCE ROW LEVEL SECURITY applies to it too.
-- app_rw        what the running application connects as. No BYPASSRLS.
-- app_platform  platform jobs (price feed, billing, cross-tenant ops). BYPASSRLS; used only
--               from platform modules, never from tenant request handling.
--
-- The database is created if missing; an existing (empty) one is handed to app_owner.

\set ON_ERROR_STOP on

CREATE ROLE app_owner LOGIN PASSWORD :'owner_pw' CREATEDB NOSUPERUSER NOBYPASSRLS;
CREATE ROLE app_rw LOGIN PASSWORD :'rw_pw' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE app_platform LOGIN PASSWORD :'platform_pw' NOSUPERUSER BYPASSRLS;

SELECT format('CREATE DATABASE %I OWNER app_owner ENCODING %L TEMPLATE template0', :'dbname', 'UTF8')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname') \gexec
ALTER DATABASE :"dbname" OWNER TO app_owner;

\connect :"dbname"

-- PostgreSQL 15+: public is owned by pg_database_owner, i.e. app_owner from here on.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_rw, app_platform;

-- Every table and sequence app_owner creates from now on is usable by the other two roles.
-- No TRUNCATE, no DDL, no ownership: app_rw cannot disable RLS on anything.
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw, app_platform;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_rw, app_platform;

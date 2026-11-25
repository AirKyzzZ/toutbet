#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 -v app_password="$APP_DB_PASSWORD" -v dbname="$POSTGRES_DB" \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE toutbet_app LOGIN PASSWORD :'app_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO toutbet_app;
SQL

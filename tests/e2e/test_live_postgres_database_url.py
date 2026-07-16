from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

pytestmark = pytest.mark.live


def test_live_postgres_uses_database_name_and_persistent_utc_from_url() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("GOODMONEYING_LIVE_POSTGRES_TEST=1에서만 실제 PostgreSQL을 검증한다")

    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    assert database_url, "실제 PostgreSQL 검증에는 GOODMONEYING_DATABASE_URL이 필요하다"

    expected_database = conninfo_to_dict(database_url).get("dbname")
    assert expected_database
    assert expected_database != "goodmoneying", "비기본 DB 이름으로 회귀를 검증해야 한다"

    from goodmoneying_api.main import app

    with TestClient(app) as client:
        health_response = client.get("/health")
        dashboard_response = client.get("/v1/dashboard/summary")

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert dashboard_response.status_code == 200

    with psycopg.connect(database_url) as connection:
        database_state = connection.execute(
            """
            SELECT
              current_database(),
              current_user,
              pg_get_userbyid(datdba),
              current_setting('TimeZone'),
              EXISTS (
                SELECT 1
                FROM pg_db_role_setting, unnest(setconfig) AS setting
                WHERE setdatabase = (
                  SELECT oid FROM pg_database WHERE datname = current_database()
                )
                  AND setrole = 0
                  AND setting = 'TimeZone=UTC'
              ),
              (SELECT count(*) FROM pg_tables WHERE schemaname = 'public')
            FROM pg_database
            WHERE datname = current_database()
            """
        ).fetchone()

    assert database_state is not None
    database_name, current_user, database_owner, timezone, persistent_utc, table_count = (
        database_state
    )
    assert database_name == expected_database
    assert current_user == database_owner
    assert timezone == "UTC"
    assert persistent_utc is True
    assert table_count >= 1

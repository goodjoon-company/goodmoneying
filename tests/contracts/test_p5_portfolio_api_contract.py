from __future__ import annotations

from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260718000600_p5_portfolio_api_commands.sql")
OPENAPI = Path("docs/contracts/api/openapi.yaml")


def test_P5_2_포트폴리오_API_DB_명령은_멱등_증거를_보존한다() -> None:
    sql = MIGRATION.read_text()

    for column in (
        "ADD COLUMN request_id TEXT",
        "ADD COLUMN idempotency_key TEXT",
        "ADD COLUMN requested_at TIMESTAMPTZ",
        "ADD COLUMN request_hash TEXT",
    ):
        assert column in sql
    assert "CREATE UNIQUE INDEX portfolios_idempotency_key_unique" in sql
    assert "WHERE idempotency_key IS NOT NULL" in sql
    assert "portfolios_api_command_all_or_none" in sql
    assert "portfolios_request_hash_format" in sql


def test_P5_2_OpenAPI는_포트폴리오_생성_목록_계약을_공개한다() -> None:
    yaml = OPENAPI.read_text()

    assert "name: 포트폴리오/봇(Portfolio/Bot)" in yaml
    assert "/v1/portfolios:" in yaml
    assert "operationId: createPortfolio" in yaml
    assert "operationId: listPortfolios" in yaml
    assert "CreatePortfolioRequest" in yaml
    assert "PortfolioResponse" in yaml
    assert "PortfoliosResponse" in yaml

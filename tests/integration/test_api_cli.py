from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.api.dependencies import get_db
from app.cli.main import app as cli_app
from app.main import app


def test_health_api(session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cli_help_and_health() -> None:
    runner = CliRunner()
    help_result = runner.invoke(cli_app, ["--help"])
    health_result = runner.invoke(cli_app, ["health-check"])

    assert help_result.exit_code == 0
    assert "init-db" in help_result.stdout
    assert health_result.exit_code == 0
    assert "status=ok" in health_result.stdout

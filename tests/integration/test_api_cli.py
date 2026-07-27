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


def test_cli_collect_url_requires_registered_source() -> None:
    result = CliRunner().invoke(
        cli_app,
        [
            "collect-url",
            "--url",
            "https://source.test/rule",
            "--source-type",
            "regulation",
        ],
    )

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)


def test_cli_cannot_set_verified_public_during_collection(tmp_path) -> None:
    network = CliRunner().invoke(
        cli_app,
        [
            "collect-url",
            "--url",
            "https://source.test/rule",
            "--source-type",
            "regulation",
            "--source-id",
            "1",
            "--authenticity-type",
            "verified_public",
        ],
    )
    local = CliRunner().invoke(
        cli_app,
        [
            "collect-directory",
            "--path",
            str(tmp_path),
            "--source-type",
            "regulation",
            "--authenticity-type",
            "verified_public",
        ],
    )

    assert network.exit_code == 2
    assert local.exit_code == 2
    assert "No such option" in network.output
    assert "No such option" in local.output

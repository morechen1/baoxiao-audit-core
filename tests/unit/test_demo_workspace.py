from fastapi.testclient import TestClient

from app.main import app


def test_contest_demo_workspace_and_assets_are_served() -> None:
    client = TestClient(app)

    workspace = client.get("/")
    stylesheet = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert workspace.status_code == 200
    assert "保销智审" in workspace.text
    assert "新建智能审核" in workspace.text
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert "demoCases" in script.text

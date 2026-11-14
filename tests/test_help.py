"""The guides as pages, and the Help routes."""
import webbrowser
from fastapi.testclient import TestClient
from cladia import help
from cladia.server import app


def test_guide_pages():
    client = TestClient(app)
    page = client.get("/guide")
    assert page.status_code == 200 and "<h1" in page.text and 'id="importing-files"' in page.text
    assert 'href="/guide/installation"' in page.text and ".md" not in page.text.split("<body>")[1].replace("cladia.log", "")[:3000]
    assert "Installing Cladia" in client.get("/guide/installation").text
    assert client.get("/guide/nothing").status_code == 404
    assert client.get("/api/library").status_code == 200  # the static mount and API still answer


def test_open_guide_uses_this_server(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    assert TestClient(app).post("/api/help/guide").status_code == 200
    assert opened == ["http://testserver/guide"]


def test_show_logs_opens_cladias_folder(monkeypatch):
    opened = []
    monkeypatch.setattr(help.os, "startfile", opened.append, raising=False)
    path = TestClient(app).post("/api/help/logs").json()["path"]
    assert [str(p) for p in opened] == [path] and path == str(help.config.APP_DIR)

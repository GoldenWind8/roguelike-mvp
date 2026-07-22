from pathlib import Path

from fastapi.testclient import TestClient

import backend.main as main


def test_production_app_mounts_public_art():
    assert any(getattr(route, "path", None) == "/art" for route in main.app.routes)


def test_root_serves_react_build(monkeypatch, tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text("<main>Emberhollow React client</main>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)

    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert "Emberhollow React client" in response.text


def test_root_explains_how_to_build_missing_frontend(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)

    response = TestClient(main.app).get("/")

    assert response.status_code == 503
    assert "npm run build" in response.json()["detail"]

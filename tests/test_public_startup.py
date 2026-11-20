"""Public-edition startup and documentation smoke tests."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from astock_hub.market.service import list_available_strategies
from astock_hub.webapi import app

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def test_owner_session_has_no_login_wall():
    response = client.get("/api/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == "owner"
    assert body["need_bootstrap"] is False


def test_removed_pool_and_trading_routes_are_not_exposed():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any(path.startswith("/api/tq") for path in paths)
    assert not any(path.startswith("/api/local-docs") for path in paths)
    response = client.get("/api/pool/topics")
    assert response.status_code == 404


def test_strategy_catalog_imports_with_declared_dependencies():
    assert list_available_strategies()


def test_readme_image_links_exist():
    for name in ("README.md", "README_EN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for relative in re.findall(r"(?:src=\"|\]\()(docs/images/[^\")]+)", text):
            assert (ROOT / relative).is_file(), f"{name}: missing {relative}"


def test_one_click_installers_are_present_and_local_by_default():
    for name in ("install.cmd", "install.ps1", "install.sh"):
        assert (ROOT / name).is_file()
    for name in ("install.ps1", "install.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "127.0.0.1" in text
        assert not re.search(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]", text)


def test_public_config_has_no_machine_specific_paths_or_secrets():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert not re.search(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]", text)
    assert "TDX-" not in text

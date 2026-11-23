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
    for prefix in ("/api/tq", "/api/gm", "/api/local-docs", "/api/license/",
                   "/api/digest-pool", "/api/manual-flags", "/api/kph-",
                   "/api/zsxq/digest", "/api/zsxq/week", "/api/zsxq/report",
                   "/api/pool/"):
        assert not any(path.startswith(prefix) for path in paths), prefix
    response = client.get("/api/pool/topics")
    assert response.status_code == 404


def test_public_frontend_has_no_private_or_strategy_api_calls():
    forbidden = ("/api/tq", "/api/gm", "/api/paper", "/api/backtest",
                 "/api/digest-pool", "/api/manual-flags", "/api/local-docs",
                 "/api/kph-", "/api/license/", "/api/pool/")
    for relative in ("frontend/src/lib/api.ts",
                     "frontend/src/components/dashboard/WatchBar.tsx"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for endpoint in forbidden:
            assert endpoint not in text, f"{relative}: {endpoint}"


def test_optional_public_adapters_import_without_removed_modules():
    from astock_hub.adapters.zsxq import ZsxqAdapter
    from astock_hub.chat.context import _norm

    assert _norm(" Ａ ") == "A"
    assert "zsxq_output" in str(ZsxqAdapter({}).output_dir)


def test_strategy_catalog_imports_with_declared_dependencies():
    assert list_available_strategies()


def test_acknowledgement_projects_and_authors_are_present():
    required = (
        "TradingAgents-astock", "国家队持仓估计", "investment-news",
        "shy3130/tick-stock-panel", "simonlin1212/a-stock-data",
    )
    for relative in ("README.md", "THIRD_PARTY_NOTICES.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "鸣谢" in text
        for project in required:
            assert project in text, f"{relative}: {project}"
        section = text.split("## 鸣谢", 1)[1].split("\n## ", 1)[0]
        assert "](http" not in section


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


def test_public_sources_have_no_machine_specific_paths():
    files = (
        "astock_hub/adapters/zsxq.py", "astock_hub/adapters/national_team.py",
        "astock_hub/adapters/investment_news.py", "astock_hub/mcp_server.py",
        "astock_hub/adapters/tickflow_cloud.py", "astock_hub/serenity.py",
        "astock_hub/market/strategies/group_doc1.py", "scripts/restart-panel.ps1",
    )
    for relative in files:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert not re.search(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]", text), relative

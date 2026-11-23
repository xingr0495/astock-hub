import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import polars as pl

from astock_hub import webapi
from astock_hub.adapters import global_index
from astock_hub.store import market_data


def test_symbol_names_falls_back_to_stale_cache(monkeypatch, tmp_path):
    cache = tmp_path / "name_map.parquet"
    pl.DataFrame({"symbol": ["300498.SZ"], "name": ["温氏股份"]}).write_parquet(cache)
    old = datetime.now(timezone.utc) - timedelta(days=3)
    os.utime(cache, (old.timestamp(), old.timestamp()))

    class FailingAdapter:
        def symbol_list(self, offset, limit):
            return SimpleNamespace(status="unavailable", data={})

    monkeypatch.setattr(market_data, "_adapter", lambda cfg: FailingAdapter())
    cfg = {"hithink_data": {"cache_dir": str(tmp_path)}}

    frame = market_data.symbol_names(cfg)

    assert frame.to_dicts() == [{"symbol": "300498.SZ", "name": "温氏股份"}]


def test_indexes_fall_back_to_tencent_when_hithink_is_missing(monkeypatch):
    class MissingHithink:
        def _run(self, args):
            raise RuntimeError("PATH 中找不到 hithink-finance")

    monkeypatch.setattr(webapi, "_get_adapters", lambda: {"hithink": MissingHithink()})
    monkeypatch.setattr(global_index, "quotes", lambda symbols: [
        {"symbol": symbol, "name": symbol, "price": 1234.5,
         "prev_close": 1200.0, "change_pct": 2.88, "amount": 1.0,
         "timestamp": None}
        for symbol in symbols
    ])

    response = webapi.api_indexes()

    assert response.status_code == 200
    body = json.loads(response.body)
    items = body["data"]["item"]
    assert len(items) == 6
    assert items[0]["thscode"] == "000001.SH"
    assert items[0]["last_price"] == 1234.5
    assert items[0]["price_change_ratio_pct"] == 2.88

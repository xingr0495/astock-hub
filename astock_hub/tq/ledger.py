# astock_hub/tq/ledger.py
"""委托幂等账本：data/tq/ledger/YYYY-MM-DD.jsonl（追加写，坏行跳过）。

只把真正提交到 TQ 的单记为 submitted 并计入当日笔数；rejected（闸口拒）也留痕可审计。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def _today_file(ledger_dir: Path) -> Path:
    d = Path(ledger_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{date.today().isoformat()}.jsonl"


def _iter_file(f: Path):
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            yield json.loads(line)
        except Exception:  # noqa: BLE001 — 坏行跳过（半截写入防御）
            continue


def _iter_entries(ledger_dir: Path):
    yield from _iter_file(_today_file(ledger_dir))


def record(ledger_dir: Path, entry: dict) -> None:
    entry = dict(entry)
    entry.setdefault("ts", date.today().isoformat())  # 简化：日期即可，秒级在 webapi 补
    f = _today_file(ledger_dir)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def iter_recent(ledger_dir: Path, limit: int = 200) -> list[dict]:
    """历史委托回看：全部日期文件，新日期在前、文件内新记录在前，limit 截断。"""
    d = Path(ledger_dir)
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.jsonl"), reverse=True):
        for entry in reversed(list(_iter_file(f))):
            entry.setdefault("date", f.stem)
            out.append(entry)
            if len(out) >= limit:
                return out
    return out


def is_submitted(ledger_dir: Path, intent_id: str) -> bool:
    return any(e.get("intent_id") == intent_id and e.get("state") in
               ("submitted", "pending_confirm") for e in _iter_entries(ledger_dir))


def count_today(ledger_dir: Path) -> int:
    return sum(1 for e in _iter_entries(ledger_dir) if e.get("state") in
               ("submitted", "pending_confirm"))

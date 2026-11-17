"""自选股与分组（服务端存储，跨浏览器/设备同步），按用户隔离。

存储：data/watchlist/<username>.json（每用户一文件，锚定包根目录，不依赖 cwd）。
data/watchlist.json 为旧全局文件，仅作管理员首访惰性迁移的源（复制，旧文件保留）。
结构：{"items": [{symbol, name, group, added_at}], "groups": [{id, name}]}
默认分组 id=default 不可删除；删除分组时组内股票移回默认分组。

带 `_for` 后缀的对外函数按用户读写；原无参函数保留走旧全局文件（兼容旧调用/旧测试）。
`_LOCK` 为全局可重入锁：持锁内先 `_ensure_user` 再读改写，跨用户写不互踩。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "data" / "watchlist"      # 每用户一文件
_FILE = Path(__file__).resolve().parents[1] / "data" / "watchlist.json"  # 旧全局（迁移源）
_LOCK = threading.RLock()

_DEFAULT_GROUP = {"id": "default", "name": "默认分组"}


def _user_file(username: str) -> Path:
    # 用户名受 auth 白名单 ^[a-zA-Z0-9_-]{2,32}$ 约束，无路径注入面；
    # 统一 casefold：Windows NTFS 大小写不敏感，大小写变体必须指向同一文件
    return _DIR / f"{username.casefold()}.json"


def _ensure_user(username: str, is_admin: bool) -> Path:
    """返回该用户的文件路径；管理员首访且旧全局文件在 → 惰性迁移一次（copy，保留旧文件）。

    只在持 `_LOCK` 时调用（与读改写同锁，防并发双拷）。
    """
    f = _user_file(username)
    if not f.exists() and is_admin and _FILE.exists():
        _DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_FILE, f)   # 复制而非移动：旧文件留作回滚保险（规格 §6）
    return f


def _load_file(f: Path) -> dict:
    """读指定文件（不持锁，由调用方持锁）。"""
    data: dict = {"items": [], "groups": [dict(_DEFAULT_GROUP)]}
    if f.exists():
        try:
            loaded = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update({k: v for k, v in loaded.items()
                             if k in ("items", "groups") and isinstance(v, list)})
        except (OSError, ValueError):
            pass
    if not any(g.get("id") == "default" for g in data["groups"]):
        data["groups"].insert(0, dict(_DEFAULT_GROUP))
    return data


def _save_file(f: Path, data: dict) -> None:
    """原子写指定文件（不持锁，由调用方持锁）。"""
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(f)


def _load() -> dict:
    with _LOCK:
        return _load_file(_FILE)


def _save(data: dict) -> None:
    with _LOCK:
        _save_file(_FILE, data)


def _norm_symbol(symbol: str) -> str:
    return "".join(c for c in str(symbol or "") if c.isdigit())[:6]


# ---------------- 旧全局文件接口（兼容旧调用/旧测试） ----------------

def get_state() -> dict:
    return _load()


def add_item(symbol: str, name: str = "", group: str = "default") -> dict:
    with _LOCK:
        return _add_item(_FILE, symbol, name, group)


def remove_item(symbol: str) -> dict:
    with _LOCK:
        return _remove_item(_FILE, symbol)


def set_item_group(symbol: str, group: str) -> dict:
    with _LOCK:
        return _set_item_group(_FILE, symbol, group)


def add_group(name: str) -> dict:
    with _LOCK:
        return _add_group(_FILE, name)


def delete_group(group_id: str) -> dict:
    with _LOCK:
        return _delete_group(_FILE, group_id)


def import_state(items: list, groups: list) -> dict:
    with _LOCK:
        return _import_state(_FILE, items, groups)


# ---------------- 按用户接口（多账号隔离） ----------------

def get_state_for(username: str, is_admin: bool = False) -> dict:
    with _LOCK:
        return _load_file(_ensure_user(username, is_admin))


def add_item_for(username: str, symbol: str, name: str = "",
                 group: str = "default", is_admin: bool = False) -> dict:
    with _LOCK:
        return _add_item(_ensure_user(username, is_admin), symbol, name, group)


def remove_item_for(username: str, symbol: str,
                    is_admin: bool = False) -> dict:
    with _LOCK:
        return _remove_item(_ensure_user(username, is_admin), symbol)


def set_item_group_for(username: str, symbol: str, group: str,
                       is_admin: bool = False) -> dict:
    with _LOCK:
        return _set_item_group(_ensure_user(username, is_admin), symbol, group)


def add_group_for(username: str, name: str, is_admin: bool = False) -> dict:
    with _LOCK:
        return _add_group(_ensure_user(username, is_admin), name)


def delete_group_for(username: str, group_id: str,
                     is_admin: bool = False) -> dict:
    with _LOCK:
        return _delete_group(_ensure_user(username, is_admin), group_id)


def import_state_for(username: str, items: list, groups: list,
                     is_admin: bool = False) -> dict:
    with _LOCK:
        return _import_state(_ensure_user(username, is_admin), items, groups)


# ---------------- 核心操作（路径参数化，均在持锁下由包装函数调用） ----------------

def _add_item(f: Path, symbol: str, name: str, group: str) -> dict:
    data = _load_file(f)
    symbol = _norm_symbol(symbol)
    if len(symbol) != 6:
        raise ValueError(f"股票代码格式不对：{symbol}（应为 6 位数字）")
    if not any(g["id"] == group for g in data["groups"]):
        group = "default"
    existing = next((i for i in data["items"] if i["symbol"] == symbol), None)
    if existing is not None:
        # 幂等：已存在则仅更新名称/分组，不重复添加
        if name:
            existing["name"] = name
        existing["group"] = group
    else:
        data["items"].append({
            "symbol": symbol, "name": name or "", "group": group,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    _save_file(f, data)
    return data


def _remove_item(f: Path, symbol: str) -> dict:
    data = _load_file(f)
    symbol = _norm_symbol(symbol)
    data["items"] = [i for i in data["items"] if i["symbol"] != symbol]
    _save_file(f, data)
    return data


def _set_item_group(f: Path, symbol: str, group: str) -> dict:
    data = _load_file(f)
    symbol = _norm_symbol(symbol)
    if not any(g["id"] == group for g in data["groups"]):
        raise ValueError(f"分组不存在：{group}")
    item = next((i for i in data["items"] if i["symbol"] == symbol), None)
    if item is None:
        raise ValueError(f"自选股里没有 {symbol}")
    item["group"] = group
    _save_file(f, data)
    return data


def _add_group(f: Path, name: str) -> dict:
    data = _load_file(f)
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名称不能为空")
    if any(g["name"] == name for g in data["groups"]):
        raise ValueError(f"分组「{name}」已存在")
    gid = "g" + format(int(time.time() * 1000), "x")[-8:]
    data["groups"].append({"id": gid, "name": name})
    _save_file(f, data)
    return data


def _delete_group(f: Path, group_id: str) -> dict:
    data = _load_file(f)
    if group_id == "default":
        raise ValueError("默认分组不能删除")
    data["items"] = [({**i, "group": "default"} if i["group"] == group_id else i)
                     for i in data["items"]]
    data["groups"] = [g for g in data["groups"] if g["id"] != group_id]
    _save_file(f, data)
    return data


def _import_state(f: Path, items: list, groups: list) -> dict:
    """迁移/批量导入（浏览器 localStorage 旧数据或批量添加）：按代码去重合并。"""
    data = _load_file(f)
    for g in groups or []:
        gid, gname = str(g.get("id") or ""), str(g.get("name") or "").strip()
        if gid and gname and gid != "default" and not any(x["id"] == gid for x in data["groups"]):
            if not any(x["name"] == gname for x in data["groups"]):
                data["groups"].append({"id": gid, "name": gname})
    known = {i["symbol"] for i in data["items"]}
    for it in items or []:
        symbol = _norm_symbol(it.get("symbol", ""))
        if len(symbol) != 6 or symbol in known:
            continue
        group = it.get("group") if any(g["id"] == it.get("group") for g in data["groups"]) else "default"
        data["items"].append({
            "symbol": symbol, "name": str(it.get("name") or ""), "group": group,
            "added_at": str(it.get("added_at") or time.strftime("%Y-%m-%dT%H:%M:%S")),
        })
        known.add(symbol)
    _save_file(f, data)
    return data

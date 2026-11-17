# -*- coding: utf-8 -*-
"""TdxQuant（通达信量化数据 tdxaidata/TdxAiData）付费源接入（2026-10-29）。

参考 tick-stock-panel backend/app/plugins/tdxquant/{client,probe}.py：
- tdxaidata = ctypes 包装的私有 TCP 协议客户端（aihs/aids.tdx.com.cn），
  DLL 与运行时 ini 随 wheel 分发（lib/ 目录，全平台三 DLL）；
- 原生层**每进程只加载一次**且 token 绑在 DLL 旁 ini → Key 探测必须
  独立子进程（临时 lib 目录 + TDX_AI_DATA_LIB 指向），结论 JSON 回传；
- 原生握手约 30s（同 IP 并发疑似服务端节流），探测超时给 75s；
- TDX 会话按 IP 复用：同 IP 活跃好 Key 会话时错误 Key 也能拉到数据，
  "无效 Key 拒绝"不可靠——探测只验**格式 + 连通性**，Key 真伪由首次
  实拉暴露。

本模块只做 Key 探测（数据源凭证页"测试连接"用）；作为行情源取数的
provider 接入留待后续（axdata 已是主源，此处不抢路由）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

LIB_ENV = "TDX_AI_DATA_LIB"  # tdxaidata 读取: 指向 DLL 文件路径
_PROBE_ENV_KEY = "TDX_QUANT_PROBE_KEY"
_PROBE_ENV_TMP = "TDX_QUANT_PROBE_TMP"
_PROBE_TIMEOUT_S = 75.0  # 原生握手实测 ~30s，必须 >45s
_RETRIES = 3
_RETRY_SLEEP_S = 1.2

_DLL_BY_PLATFORM = {"win32": "TdxAiData.dll", "darwin": "libTdxAiData.dylib"}
LIB_DLL_NAME = _DLL_BY_PLATFORM.get(sys.platform, "libTdxAiData.so")


class TdxQuantError(RuntimeError):
    """tdxaidata 调用失败（网络/参数/协议）。"""


def check_key_format(api_key: str) -> tuple[bool, str]:
    """Key 格式关：TDX- 开头、总长 ≥12（通达信商城数据服务 Key）。"""
    key = (api_key or "").strip()
    if not key:
        return False, "Key 为空"
    if not key.startswith("TDX-") or len(key) < 12:
        return False, "Key 格式应为 TDX- 开头（通达信商城创建的数据服务 Key）"
    return True, "ok"


def package_lib_dir() -> Path:
    """tdxaidata 包自带的 lib 目录（平台 DLL + 运行时 ini）。"""
    import tdxaidata

    return Path(tdxaidata.__file__).resolve().parent / "lib"


def write_token_ini(ini: Path, api_key: str) -> None:
    """把 [Token] token=<key> 写入 ini，保留其余配置行。"""
    lines = (
        ini.read_text(encoding="utf-8", errors="replace").splitlines()
        if ini.exists()
        else ["[Token]"]
    )
    out: list[str] = []
    in_token = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == "[token]":
            in_token = True
            out.append(line)
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_token = False
        if in_token and "=" in line and stripped.lower().split("=", 1)[0].strip() == "token":
            if not replaced:
                out.append(f"token={api_key}")
                replaced = True
            continue
        out.append(line)
    if "[Token]" not in {line.strip() for line in out}:
        out.extend(["[Token]", f"token={api_key}"])
    elif not replaced:
        idx = next(i for i, line in enumerate(out) if line.strip().lower() == "[token]")
        out.insert(idx + 1, f"token={api_key}")
    ini.write_text("\n".join(out) + "\n", encoding="utf-8")


def materialize_lib(api_key: str, lib_root: Path, source: Path | None = None) -> Path:
    """复制包内 lib 到私有目录并写入 token，返回 DLL 路径。

    marker 记 token 指纹 + DLL mtime/size，未变化时直接复用（不反复拷
    几十 MB 的 DLL）。
    """
    src = source or package_lib_dir()
    dll = src / LIB_DLL_NAME
    if not dll.exists():
        raise TdxQuantError(f"tdxaidata lib 缺少 {LIB_DLL_NAME}: {src}")
    lib_root.mkdir(parents=True, exist_ok=True)
    marker = lib_root / ".tdxquant_lib_marker"
    stat = dll.stat()
    want = f"{hashlib.sha256(api_key.encode()).hexdigest()}:{stat.st_mtime_ns}:{stat.st_size}"
    copied = lib_root / LIB_DLL_NAME
    if marker.exists() and marker.read_text(encoding="utf-8") == want and copied.exists():
        return copied
    for item in src.iterdir():
        if item.is_file():
            shutil.copy2(item, lib_root / item.name)
    for ini_name in ("TdxAiData.ini", "user.ini"):
        write_token_ini(lib_root / ini_name, api_key)
    marker.write_text(want, encoding="utf-8")
    return copied


def run_worker() -> int:
    """探测 worker —— 必须在独立子进程内执行（DLL 每进程一个 token）。

    从 env 读候选 Key 与临时目录，结论以单行 JSON 打到 stdout。
    """
    key = os.environ.pop(_PROBE_ENV_KEY, "")
    tmp = os.environ.pop(_PROBE_ENV_TMP, "")
    try:
        dll = materialize_lib(key, Path(tmp))
        os.environ[LIB_ENV] = str(dll)
        from tdxaidata import tqs
        from tdxaidata.tdxaidata import _NativeTdxAiData

        native = _NativeTdxAiData.get_instance()
        # 连通性判定：快照端点拿到数据 = 服务器可达 + DLL 正常。
        # 轮询容忍 DLL 冷启动的"未就绪"窗口（实测首批整批失败）。
        reachable = False
        last_err = ""
        for _ in range(_RETRIES):
            try:
                if native.get_market_snapshot_batch("600519.SH", "Now"):
                    reachable = True
                    break
            except Exception as exc:  # noqa: BLE001 — 原生 ctypes 层异常不可枚举
                last_err = str(exc)[:200]
            time.sleep(_RETRY_SLEEP_S)
        if not reachable:
            print(json.dumps({"ok": False, "reason": f"行情服务器连接失败：{last_err or '无响应'}"}))
            return 0
        # 日K辅助信号（会话按 IP 复用时错误 Key 也能过，不作拒绝依据）
        aux = ""
        try:
            raw = native.get_his_dats("600519.SH", "", "2026-01-01 00:00:00", "1d", "none", 3)
            v = tqs._clean(raw)
            aux = "his_ok" if isinstance(v, dict) and v.get("Date") else "his_empty"
        except Exception:  # noqa: BLE001
            aux = "his_exc"
        print(json.dumps({"ok": True, "reason": "", "aux": aux}))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": str(exc)[:300]}))
    return 0


def _tail(text: str, limit: int = 150) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1][:limit] if lines else "(空)"


def probe(api_key: str, timeout_s: float = _PROBE_TIMEOUT_S) -> tuple[bool, str]:
    """子进程探测候选 Key：格式由调用方把关，这里验连通性（快照可达）。"""
    ok, msg = check_key_format(api_key)
    if not ok:
        return False, msg
    repo_root = Path(__file__).resolve().parents[2]
    tmp_dir = tempfile.mkdtemp(prefix="tdxquant_probe_")
    env = {
        **os.environ,
        _PROBE_ENV_KEY: api_key.strip(),
        _PROBE_ENV_TMP: tmp_dir,
        "PYTHONPATH": str(repo_root),
    }
    script = "from astock_hub.adapters.tdxquant import run_worker; raise SystemExit(run_worker())"
    try:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=timeout_s,
                cwd=str(repo_root), env=env,
            )
        except subprocess.TimeoutExpired:
            return False, (
                f"探测超时（{timeout_s:.0f}s）——原生握手约需 30 秒，"
                f"请检查网络后重试；若持续超时可直接保存，Key 有效性以首次实拉为准"
            )
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    break
                if payload.get("ok"):
                    aux = payload.get("aux") or ""
                    note = "；日K通道正常" if aux == "his_ok" else ""
                    return True, f"连通正常{note}（Key 有效性以首次实拉数据为准）"
                return False, f"探测失败：{payload.get('reason', '未知原因')}"
        return False, (
            f"探测失败：子进程死于原生层（exit={proc.returncode}），"
            f"stderr={_tail(proc.stderr)}"
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


__all__ = ["TdxQuantError", "check_key_format", "materialize_lib",
           "package_lib_dir", "probe", "run_worker", "write_token_ini"]

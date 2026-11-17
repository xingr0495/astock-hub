"""数据刷新：分析前把各路源数据更新到最新。

各源的刷新方式（只调用源项目现成的脚本，不改源项目）：
- etf（国家队持仓估计）：跑 national_team_position.py --data-only（约 2-3 分钟）
- zsxq：zsxq-cli（官方 MCP，OAuth 设备码授权）补抓目标日原始帖子并自动 AI 汇总
  （凌晨 <06:00 抓昨天、其余时段抓当天；0:00 与 12:00 各一次定时）
- national_team（透视系统 PG）：跑 etl_ingest_tushare.py + analysis_engine.py（长任务，仅显式指定时运行）
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from . import errors, models
from .adapters.base import timed
from .zsxq_fetch import fetch_topics, resolve_output_dir

QUICK_SOURCES = ["etf", "zsxq", "tdx_daily", "news"]
ALL_SOURCES = ["etf", "zsxq", "news", "national_team", "hithink_data", "tdx_daily"]


def _run(cmd: list[str], cwd: str | None, timeout: int,
         extra_env: dict | None = None) -> tuple[int, str]:
    """跑外部命令，返回 (退出码, 输出尾巴)。stdin 置空防阻塞。"""
    import os
    env = dict(os.environ)
    # 子进程 stdout 被管道捕获时 Windows 默认 GBK 编码，中文/emoji 打印会崩；
    # 统一强制 UTF-8（源项目脚本自身读写文件都是 utf-8）
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, timeout=timeout,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        env=env,
    )
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out[-1500:]


def _result(source: str, ran: bool, before: str | None, after: str | None,
            note: str, ok: bool = True, latency_ms: int | None = None) -> models.DimensionResult:
    status = models.OK if ok else models.UNAVAILABLE
    if not ran and ok:
        status = models.SKIPPED
    return models.DimensionResult(
        status=status, source=source,
        meta=models.SourceMeta(as_of=after, latency_ms=latency_ms),
        data={"ran": ran, "as_of_before": before, "as_of_after": after, "note": note},
        error=None if ok else errors.make_error(errors.UPSTREAM_ERROR, note),
    )


def refresh_etf(cfg: dict, force: bool = False) -> models.DimensionResult:
    """宽基 ETF 份额刷新（2-3 分钟，打上交所接口）。"""
    from .adapters.national_team import NationalTeamAdapter
    nt_cfg = cfg.get("national_team", {})
    adapter = NationalTeamAdapter(nt_cfg)
    before = adapter.etf_shares(weeks=1)
    before_as_of = before.meta.as_of
    fresh = (before.status == models.OK and before.meta.freshness
             and not before.meta.freshness.get("stale"))
    if fresh and not force:
        return _result("refresh-etf", False, before_as_of, before_as_of,
                       "数据在新鲜度容忍范围内（周频），未重跑；--force 可强制刷新")

    python = nt_cfg.get("etf_python")
    script_dir = nt_cfg.get("etf_project_dir")
    if not python or not Path(python).exists() or not script_dir:
        return _result("refresh-etf", False, before_as_of, before_as_of,
                       f"找不到运行环境：{python}（检查 config.toml [national_team] 的 etf_python/etf_project_dir）",
                       ok=False)
    try:
        with timed() as t:
            code, tail = _run(
                [python, "scripts/national_team_position.py", "--data-only",
                 "--output-dir", script_dir],
                cwd=script_dir, timeout=600)
    except subprocess.TimeoutExpired:
        return _result("refresh-etf", False, before_as_of, before_as_of,
                       "刷新超时（10 分钟），上交所接口可能繁忙，稍后重试", ok=False)
    if code != 0:
        return _result("refresh-etf", False, before_as_of, before_as_of,
                       f"刷新脚本退出码 {code}：{tail[-300:]}", ok=False)
    after = adapter.etf_shares(weeks=1)
    return _result("refresh-etf", True, before_as_of, after.meta.as_of,
                   f"ETF 份额已刷新到 {after.meta.as_of}", latency_ms=t["ms"])


def _default_target_day(now: datetime | None = None) -> str:
    """定时抓取的目标交易日（自然日）：凌晨（<06:00）抓昨天，其余时段抓当天。

    0:00 定时若抓"当天"，跨午夜时新一天还没有帖子、几乎必空（2026-11-06 核对
    代码发现注释写"前一天"实现却是 date.today()）；12:00 午盘加跑一次抓当天。
    """
    now = now or datetime.now()
    day = now.date() - timedelta(days=1) if now.hour < 6 else now.date()
    return day.isoformat()


def refresh_zsxq(cfg: dict, force: bool = False, day: str | None = None) -> models.DimensionResult:
    """补抓知识星球原始帖子，并自动做 AI 汇总（2026-11-06 用户拍板）。

    目标日：day 显式指定优先；否则按 _default_target_day（凌晨抓昨天、白天抓当天）。
    已有原始数据也会补一次汇总（summarize_day 幂等：已汇总跳过、无帖跳过）。
    AI 汇总失败只记 note，不影响抓取结果，下轮定时再补。
    """
    zx_cfg = cfg.get("zsxq", {})
    group_id = zx_cfg.get("group_id", "15552822451452")
    output_dir = resolve_output_dir(zx_cfg)
    today = day or _default_target_day()
    # 抓"昨天"（或更早）强制重抓补全：中午抓的是当天截至中午的帖子，下午/晚上的
    # 要靠次日 0:00 补——原"文件已存在即跳过"会把半日文件当完整（2026-11-09 实测
    # 11-08 只有 14/122 帖）；当天目标保持幂等跳过（盘中反复刷新不重复拉）。
    if today < date.today().isoformat():
        force = True
    topics_file = output_dir / f"{today}_topics.json"
    if topics_file.exists() and not force:
        result = _result("refresh-zsxq", False, today, today,
                         f"{today} 原始帖子已抓取，未重跑；--force 可强制")
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            with timed() as t:
                result_topics = fetch_topics(group_id, today)
            topics_file.write_text(json.dumps(result_topics, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except subprocess.TimeoutExpired:
            return _result("refresh-zsxq", False, None, None,
                           "抓取超时，稍后重试", ok=False)
        except RuntimeError as e:
            return _result("refresh-zsxq", False, None, None,
                           f"{e}；若提示未认证，运行 zsxq-cli auth login 重新授权", ok=False)
        note = f"已抓取 {today} 原始帖子（zsxq-cli 官方API）"
        if topics_file.exists():
            note += f"（{topics_file.name}）"
        result = _result("refresh-zsxq", True, None, today, note, latency_ms=t["ms"])

    # 自动 AI 汇总（生成日报 md + 提取个股进纪要池）：尽力而为，失败不拖垮刷新
    if topics_file.exists():
        try:
            from .zsxq_digest import summarize_day
            sres = summarize_day(cfg, today, force=force)
            note = result.data.get("note", "")
            result.data["note"] = f"{note}；AI 汇总：{sres.get('note', '')}"
            result.data["summary"] = {k: sres.get(k) for k in ("ok", "ran", "path")}
        except Exception as e:  # noqa: BLE001 —— 汇总异常不许让刷新链失败
            result.data["note"] = f"{result.data.get('note', '')}；AI 汇总异常：{e}"
    return result


def refresh_national_team(cfg: dict, force: bool = False) -> models.DimensionResult:
    """透视系统 PG 刷新：ETL + 分析引擎（长任务，可能 10 分钟以上）。

    已停用（2026-09-29 起）：ETL 维护成本高（tushare 限流/东财风控静默失败）。
    """
    nt_cfg = cfg.get("national_team", {})
    if not nt_cfg.get("enabled", True):
        return _result("refresh-national-team", False, None, None,
                       "国家队持仓透视系统已停用（config.toml [national_team] enabled=false），"
                       "不再跑 ETL。国家队维度用「国家队持仓估计」ETF 份额（etf 源）")
    python = nt_cfg.get("python")
    project_dir = nt_cfg.get("project_dir")
    if not python or not Path(python).exists() or not project_dir:
        return _result("refresh-national-team", False, None, None,
                       f"找不到运行环境：{python}（检查 config.toml [national_team] 的 python/project_dir）",
                       ok=False)
    try:
        with timed() as t:
            code1, tail1 = _run([python, "etl_ingest_tushare.py"],
                                cwd=project_dir, timeout=1500)
            if code1 != 0:
                return _result("refresh-national-team", False, None, None,
                               f"ETL 退出码 {code1}：{tail1[-300:]}", ok=False)
            code2, tail2 = _run([python, "analysis_engine.py"],
                                cwd=project_dir, timeout=600)
        if code2 != 0:
            return _result("refresh-national-team", False, None, None,
                           f"分析引擎退出码 {code2}：{tail2[-300:]}", ok=False)
    except subprocess.TimeoutExpired:
        return _result("refresh-national-team", False, None, None,
                       "刷新超时（ETL 25 分钟上限），数据量大属正常，可隔段时间再查 health 确认",
                       ok=False)
    from .adapters.national_team import NationalTeamAdapter
    after = NationalTeamAdapter(nt_cfg).health()
    return _result("refresh-national-team", True, None, after.meta.as_of,
                   f"透视系统已刷新到 {after.meta.as_of}", latency_ms=t["ms"])


def refresh_investment_news(cfg: dict, force: bool = False) -> models.DimensionResult:
    """Investment News 刷新（2026-10-24 收编后进程内调用 news_pipe）：
    抓 106 源 → 大模型出中文要点/翻译，产物 data/investment_news/news.json。

    "今天已跑过"检查（generated_at == 今天 且 有 AI 要点层则跳过）。
    digest 要调大模型，耗时几分钟到十几分钟。
    """
    from .adapters.investment_news import InvestmentNewsAdapter
    adapter = InvestmentNewsAdapter({})
    before = adapter.health()
    before_as_of = before.meta.as_of
    before_has_points = bool(
        (before.data or {}).get("industries_with_points") if before.data else False)

    from datetime import date as _date
    today = _date.today().isoformat()
    if today == before_as_of and before_has_points and not force:
        return _result("refresh-news", False, before_as_of, before_as_of,
                       "今天的资讯已抓取且已有 AI 要点，未重跑；--force 强制")

    try:
        with timed() as t:
            from .news_pipe import runner as _news_runner
            _news_runner.run_fetch_pipeline()
            dr = _news_runner.run_digest(cfg)
    except Exception as e:  # noqa: BLE001 — 收编后无子进程，超时/网络/LLM 异常统一兜底
        return _result("refresh-news", False, before_as_of, None,
                       f"资讯管道失败：{str(e)[-300:]}", ok=False)
    failed_inds = dr.get("failed_industries") or []
    after = InvestmentNewsAdapter({}).health()
    if after.status == models.OK:
        note = (f"news.json 已更新：{after.data.get('generated_at')}，"
                f"{after.data.get('industries')} 个赛道，"
                f"{after.data.get('industries_with_points')} 个已有 AI 要点")
        if failed_inds:
            note += f"；{len(failed_inds)} 栏要点生成失败（{ '、'.join(failed_inds[:5]) }）"
        return _result("refresh-news", True, before_as_of, after.meta.as_of,
                       note, latency_ms=after.meta.latency_ms)
    return _result("refresh-news", True, before_as_of, after.meta.as_of,
                   f"刷新已跑完但 news.json 尚未被读到（可能是写入竞态，稍后重查 health 确认）："
                   f"{after.error.get('message') if after.error else ''}",
                   ok=True)


def refresh_hithink_data(cfg: dict, force: bool = False) -> models.DimensionResult:
    """hithink 本地全市场库增量同步（`data sync`），随后重建概念成分映射。

    长任务：data sync 会持数据锁、拉增量日线与复权因子（几分钟）；成分映射要对
    710 个指数重取成分（约十几分钟）。因此**不进 QUICK_SOURCES**，只在显式
    `--source hithink_data` 时跑。
    """
    import shutil

    exe = shutil.which("hithink-finance")
    if not exe:
        return _result("refresh-hithink-data", False, None, None,
                       "PATH 中找不到 hithink-finance CLI（先安装/配置它）", ok=False)
    env_args = {"PYTHONUTF8": "1"}
    try:
        code, tail = _run([exe, "data", "sync", "--format", "json"], None,
                          timeout=1800, extra_env=env_args)
    except subprocess.TimeoutExpired:
        return _result("refresh-hithink-data", False, None, None,
                       "data sync 超时（30 分钟上限），稍后重试或看 CLI 输出", ok=False)
    if code != 0:
        return _result("refresh-hithink-data", False, None, None,
                       f"data sync 失败：{tail[-400:]}", ok=False)

    # 重建概念+行业成分映射（快照无历史版本，sync 后要更新）
    try:
        from .store.concepts import all_membership
        summary = all_membership(cfg, force=True)
    except Exception as e:  # noqa: BLE001
        return _result("refresh-hithink-data", True, None, None,
                       f"日线已同步但成分映射重建失败：{e}", ok=True)
    return _result(
        "refresh-hithink-data", True, None, None,
        f"本地库已增量同步；成分：{summary}")


def refresh_tdx_daily(cfg: dict, force: bool = False) -> models.DimensionResult:
    """a-stock-data 通达信盘后包同步（增量，几秒至几十秒）。"""
    from .adapters.astock_data import tdx_daily_package
    from .store import parquet_store

    try:
        result = parquet_store.sync_store(cfg)
        if result.get("ok"):
            note = (f"通达信同步至 {result.get('synced_to')}，"
                    f"新增 {result.get('rows_added', 0)} 行（来源={result.get('source', '盘后包')}）")
            return _result("refresh-tdx-daily", True, "a-stock-data/tdx", None, note)
        else:
            return _result(
                "refresh-tdx-daily", False, "a-stock-data/tdx", None,
                f"通达信同步失败：{result.get('error', '未知错误')}")
    except Exception as e:
        return _result(
            "refresh-tdx-daily", False, "a-stock-data/tdx", None,
            f"通达信同步异常：{e}")


REFRESHERS = {
    "etf": refresh_etf,
    "zsxq": refresh_zsxq,
    "news": refresh_investment_news,
    "national_team": refresh_national_team,
    "hithink_data": refresh_hithink_data,
    "tdx_daily": refresh_tdx_daily,
}


def run_refresh(cfg: dict, sources: list[str] | None = None,
                force: bool = False) -> models.Envelope:
    """按序刷新指定源（默认 = 快速四源：etf/zsxq/tdx_daily/news）。"""
    sources = sources or QUICK_SOURCES
    unknown = [s for s in sources if s not in REFRESHERS]
    env = models.Envelope(command="refresh",
                          query={"sources": sources, "force": force})
    if unknown:
        env.data = {"error": f"未知数据源：{unknown}，可选：{ALL_SOURCES}"}
        return env
    results = {}
    for name in sources:
        try:
            results[name] = REFRESHERS[name](cfg, force=force)
        except Exception as e:  # 单源刷新失败不影响其他源
            results[name] = models.DimensionResult(
                status=models.UNAVAILABLE, source=f"refresh-{name}",
                error=errors.make_error(errors.UPSTREAM_ERROR, f"刷新异常：{e}"))
    env.dimensions = results
    return env

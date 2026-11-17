"""astock-hub 统一命令行入口。

用法示例：
  astock-hub health                 # 五路数据源健康检查
  astock-hub bundle 600519          # 个股五维数据包
  astock-hub overview               # 今日五维总览
  astock-hub kline 600519 --days 60
  astock-hub snapshot --out data/snapshot.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import models
from .config import ConfigError, load_config, mask_sensitive
from .core.bundle import build_bundle
from .core.overview import build_overview
from .adapters.hithink import HithinkAdapter
from .adapters.investment_news import InvestmentNewsAdapter
from .adapters.national_team import NationalTeamAdapter
from .adapters.zsxq import ZsxqAdapter


def _adapters(cfg: dict) -> dict:
    return {
        "national_team": NationalTeamAdapter(cfg.get("national_team", {})),
        "zsxq": ZsxqAdapter(cfg.get("zsxq", {})),
        "hithink": HithinkAdapter(cfg.get("hithink", {})),
        "investment_news": InvestmentNewsAdapter(cfg.get("investment_news", {})),
    }


def _wrap(command: str, dim: models.DimensionResult, query: dict | None = None) -> models.Envelope:
    env = models.Envelope(command=command, query=query or {})
    env.data = dim.to_dict()
    return env


def _emit(env: models.Envelope, out: str | None) -> None:
    """默认 stdout 打印完整 JSON；--out 时大结果落盘，stdout 只给摘要。"""
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.to_json(), encoding="utf-8")
        summary = {"saved_to": str(path), "command": env.command}
        d = env.to_dict()
        if "summary" in d:
            summary["summary"] = d["summary"]
        if "query" in d:
            summary["query"] = d["query"]
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(env.to_json())


def _status_icon(status: str) -> str:
    return {
        models.OK: "[正常]", models.STALE: "[偏旧]", models.PARTIAL: "[部分]",
        models.NOT_FOUND: "[无数据]", models.UNAVAILABLE: "[不可用]",
        models.SKIPPED: "[跳过]", models.NOT_APPLICABLE: "[不适用]",
    }.get(status, f"[{status}]")


def _cmd_health(cfg: dict, as_json: bool) -> int:
    adapters = _adapters(cfg)
    results = {name: adapter.health() for name, adapter in adapters.items()}
    if as_json:
        env = models.Envelope(command="health")
        env.data = {name: r.to_dict() for name, r in results.items()}
        print(env.to_json())
    else:
        print("astock-hub 四路数据源健康检查")
        print("-" * 60)
        for name, r in results.items():
            as_of = r.meta.as_of or "-"
            line = f"{_status_icon(r.status)} {name:<14} 数据时间: {as_of}"
            if r.meta.latency_ms is not None:
                line += f"  ({r.meta.latency_ms}ms)"
            print(line)
            if r.error:
                print(f"    原因: {r.error.get('message')}")
                if r.error.get("hint"):
                    print(f"    处理: {r.error['hint']}")
    # 有任何一路不可用返回 1（方便脚本判断），但正常输出不算崩溃
    return 1 if any(r.status == models.UNAVAILABLE for r in results.values()) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astock-hub", description="A 股五维数据聚合层（AI 优先，输出 JSON）")
    parser.add_argument("--config", help="配置文件路径（默认项目根目录 config.toml）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("refresh", help="分析前刷新数据到最新（默认快速四源：etf/zsxq/tdx_daily/news）")
    p.add_argument("--source", help="逗号分隔：etf,zsxq,news,national_team,hithink_data,tdx_daily,all"
                                    "（national_team 是长任务，需显式指定）")
    p.add_argument("--force", action="store_true", help="数据没过期也强制重跑")
    p.add_argument("--out", help="结果写入文件")

    p = sub.add_parser("health", help="四路数据源健康检查")
    p.add_argument("--json", action="store_true", help="输出结构化 JSON 而非表格")

    p = sub.add_parser("bundle", help="个股五维数据包")
    p.add_argument("code", help="6 位代码（如 600519）或股票名称")
    p.add_argument("--days", type=int, default=120, help="日K天数（默认 120）")
    p.add_argument("--out", help="结果写入文件，stdout 只打印摘要")

    p = sub.add_parser("overview", help="今日五维总览")
    p.add_argument("--out", help="结果写入文件")
    p.add_argument("--html", nargs="?", const="", metavar="PATH",
                   help="生成人看的 HTML 报告（不带路径时默认 data/overview_日期.html）")

    p = sub.add_parser("kline", help="日K（含 MA/MACD/KDJ/RSI/BOLL 等指标，本地计算）")
    p.add_argument("code")
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--out")

    p = sub.add_parser("levels", help="关键价位（支撑压力/枢轴/缺口/斐波/整数关口等 11 类）")
    p.add_argument("code")
    p.add_argument("--days", type=int, default=250)
    p.add_argument("--out")

    p = sub.add_parser("screen", help="运行内置选股策略（本地全市场计算）")
    p.add_argument("strategy", help="策略 id（用 strategies 子命令查看）")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out")

    sub.add_parser("strategies", help="列出内置选股策略目录")

    p = sub.add_parser("mainline", help="每日主线排行（概念+行业，基于涨停梯队，本地计算）")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("rps", help="板块涨幅轮动矩阵（概念+行业，本地计算）")
    p.add_argument("--days", type=int, default=12)
    p.add_argument("--refresh", action="store_true", help="重建概念成分映射")
    p.add_argument("--out")

    p = sub.add_parser("market-regime", help="市场环境：情绪分 + 状态 + 情绪周期阶段（本地计算）")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--refresh", action="store_true", help="重算目标区间（默认读落盘历史）")
    p.add_argument("--out")

    p = sub.add_parser("nt-position", help="国家队个股持仓结论")
    p.add_argument("code")

    p = sub.add_parser("nt-etf", help="宽基 ETF 份额序列（汇金仓位代理）")
    p.add_argument("--weeks", type=int, default=12)

    sub.add_parser("zsxq-latest", help="最新调研鹅纪要（元信息 + 摘录）")

    p = sub.add_parser("news", help="investment-news 各赛道最新动向（AI 中文要点 + 顶部新闻）")
    p.add_argument("--industry", help="只看某个赛道（如 ai/semi/robot/auto/space）")

    p = sub.add_parser("news-search", help="在 investment-news 新闻里搜关键词")
    p.add_argument("keyword")
    p.add_argument("--days", type=int, help="保留参数（预留）")

    p = sub.add_parser("zsxq-search", help="近期纪要关键词搜索")
    p.add_argument("keyword")
    p.add_argument("--days", type=int, default=7)

    sub.add_parser("parquet-init", help="初始化日期分区 Parquet 仓库（首次运行）")

    sub.add_parser("parquet-sync", help="增量同步 Parquet 仓库")

    p = sub.add_parser("hithink", help="hithink-finance CLI 透传（追加 --format json）")
    p.add_argument("args", nargs=argparse.REMAINDER)

    sub.add_parser("config", help="显示生效配置（密码脱敏）").add_argument(
        "--show", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 下 stdout 被重定向时默认用本地编码（GBK），强制 UTF-8 防止乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2

    adapters = _adapters(cfg)
    cmd = args.command

    if cmd == "health":
        return _cmd_health(cfg, args.json)
    if cmd == "refresh":
        from .refresh import ALL_SOURCES, QUICK_SOURCES, run_refresh
        if args.source:
            sources = ([s.strip() for s in args.source.split(",") if s.strip()]
                       if args.source != "all" else ALL_SOURCES)
        else:
            sources = QUICK_SOURCES
        _emit(run_refresh(cfg, sources=sources, force=args.force), args.out)
    if cmd == "bundle":
        _emit(build_bundle(cfg, args.code, days=args.days), args.out)
    elif cmd == "overview":
        env = build_overview(cfg)
        if args.html is not None:
            from datetime import date
            from .report import render_overview_html
            path = Path(args.html) if args.html else Path(
                "data") / f"overview_{date.today().strftime('%Y%m%d')}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_overview_html(env.to_dict()), encoding="utf-8")
            print(json.dumps({"saved_to": str(path), "command": "overview",
                              "summary": env.to_dict().get("summary")},
                             ensure_ascii=False, indent=2))
        else:
            _emit(env, args.out)
    elif cmd == "kline":
        from .core.kline import local_kline_dim
        from .symbols import normalize
        _emit(_wrap("kline", local_kline_dim(cfg, normalize(args.code), args.days),
                    {"input": args.code}), args.out)
    elif cmd == "levels":
        from .core.kline import levels_dim
        from .symbols import normalize
        _emit(_wrap("levels", levels_dim(cfg, normalize(args.code), args.days),
                    {"input": args.code}), args.out)
    elif cmd == "screen":
        from .market import service as market_service
        data = market_service.screen_strategy(cfg, args.strategy, limit=args.limit)
        _emit(_wrap("screen", models.DimensionResult(
            status=models.OK if not data.get("unavailable") else models.UNAVAILABLE,
            source="hithink+local-screen", data=data)), args.out)
    elif cmd == "strategies":
        from .market import service as market_service
        _emit(_wrap("strategies", models.DimensionResult(
            status=models.OK, source="local",
            data={"strategies": market_service.list_available_strategies()})), None)
    elif cmd == "mainline":
        from .market import service as market_service
        data = market_service.mainline_snapshot(cfg, days=args.days, refresh=args.refresh)
        _emit(_wrap("mainline", models.DimensionResult(
            status=models.OK, source="hithink+local-mainline", data=data)), args.out)
    elif cmd == "rps":
        from .market import service as market_service
        data = market_service.rps_snapshot(cfg, days=args.days, refresh=args.refresh)
        _emit(_wrap("rps", models.DimensionResult(
            status=models.OK, source="hithink+local-rps", data=data)), args.out)
    elif cmd == "market-regime":
        from .market import service as regime_service
        snap = regime_service.snapshot(cfg, days=args.days, refresh=args.refresh)
        _emit(_wrap("market-regime", models.DimensionResult(
            status=models.OK, source="hithink+local-regime", data=snap)), args.out)
    elif cmd == "nt-position":
        from .symbols import normalize
        _emit(_wrap("nt-position", adapters["national_team"].position(
            normalize(args.code).ts_code), {"input": args.code}), None)
    elif cmd == "nt-etf":
        _emit(_wrap("nt-etf", adapters["national_team"].etf_shares(args.weeks)), None)
    elif cmd == "zsxq-latest":
        _emit(_wrap("zsxq-latest", adapters["zsxq"].latest()), None)
    elif cmd == "news":
        _emit(_wrap("news", adapters["investment_news"].latest(
            industry=args.industry)), None)
    elif cmd == "news-search":
        _emit(_wrap("news-search", adapters["investment_news"].search(
            args.keyword), {"keyword": args.keyword}), None)
    elif cmd == "zsxq-search":
        _emit(_wrap("zsxq-search", adapters["zsxq"].search(
            args.keyword, days=args.days), {"keyword": args.keyword}), None)
    elif cmd == "parquet-init":
        from .store.parquet_store import init_store
        result = init_store(cfg, force=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "parquet-sync":
        from .store.parquet_store import sync_store
        result = sync_store(cfg)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "hithink":
        import shutil
        exe = cfg.get("hithink", {}).get("cli", "hithink-finance")
        exe = shutil.which(exe) or exe
        proc = subprocess.run([exe, *args.args, "--format", "json"],
                              encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL)
        return proc.returncode
    elif cmd == "config":
        print(json.dumps(mask_sensitive(cfg), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

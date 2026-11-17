# -*- coding: utf-8 -*-
"""公共 LLM 通道（OpenAI 兼容 chat/completions + 429/5xx 退避重试）。

自 zsxq_digest 抽出（2026-10-13 看板 AI 对话框 T1），zsxq 日报、本地资料摘要
共用此通道，后续 /api/chat 对话端点也走这里。密钥/后端/模型均走 config
（[llm] 段优先，回落 [ta] 段），密钥读取复用 serenity 的 _read_llm_key。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from .serenity import _read_llm_key

_GENERIC_FALLBACK = "https://api.xiaomimimo.com/v1"

# 普通用户未自备密钥时的标记错误（chat 端点识别后给友好引导文案）
OWN_KEY_REQUIRED_MSG = "未配置个人大模型密钥：请在「大模型」设置页填写自己的 API Key"

# claude -p 禁用全部工具：只让它处理我们给的文本，不读文件/不联网/不起子任务
_CLI_DISALLOW = ["Read", "Write", "Edit", "Bash", "Glob", "Grep",
                 "WebFetch", "WebSearch", "TodoWrite", "Task", "NotebookEdit"]
_CLI_ROLE_LABEL = {"system": "系统", "user": "用户", "assistant": "助手"}


def find_claude() -> str | None:
    """探测本机 claude CLI；找不到返回 None。"""
    p = shutil.which("claude")
    if p:
        return p
    home = os.path.expanduser("~")
    candidates = [
        "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
        f"{home}/.local/bin/claude", f"{home}/.claude/local/claude",
        os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                     "claude-code", "claude.exe"),
        f"{home}/.local/bin/claude.exe", f"{home}/.claude/local/claude.exe",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _messages_to_text(messages: list[dict]) -> tuple[str, str]:
    """拆出 system（合并）与其余消息拼纯文本，供 CLI 无头模式使用。"""
    sys_parts, rest = [], []
    for m in messages:
        content = m.get("content") or ""
        if m.get("role") == "system":
            sys_parts.append(content)
        else:
            label = _CLI_ROLE_LABEL.get(m.get("role"), m.get("role"))
            rest.append(f"{label}：{content}")
    return "\n\n".join(sys_parts), "\n\n".join(rest)


def call_cli(messages: list[dict], *, timeout: int = 240) -> str:
    """走本机 claude CLI 订阅通道（claude -p 无头模式）。

    未安装/未登录分类抛 RuntimeError，不做退避重试（重试由调用方负责）。
    """
    exe = find_claude()
    if not exe:
        raise RuntimeError(
            "未检测到 Claude Code 命令行；订阅通道需本机安装并 `claude login`，"
            "或在大模型设置切回 API 通道。")
    system, user_text = _messages_to_text(messages)
    sysf = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(system or "你是有帮助的助手。")
            sysf = f.name
        try:
            proc = subprocess.run(
                [exe, "-p", "--output-format", "text",
                 "--system-prompt-file", sysf,
                 "--disallowedTools", *_CLI_DISALLOW],
                input=user_text, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"claude CLI 调用超时（{timeout}s）") from e
    finally:
        if sysf:
            try:
                os.unlink(sysf)
            except OSError:
                pass
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-600:]
        low = tail.lower()
        if any(w in low for w in ("not logged in", "claude login",
                                  "unauthorized", "401", "登录")):
            raise RuntimeError(
                f"Claude 订阅未登录，请先 `claude login`：{tail[:200]}")
        raise RuntimeError(f"claude CLI 退出码 {proc.returncode}：{tail[:300]}")
    return proc.stdout or ""


def resolve_channel(cfg: dict, model: str | None = None,
                    user: dict | None = None) -> dict:
    """LLM 通道解析（2026-10-23 双通道：api / claude-cli）。

    优先级：caller 显式 model > **前端 profile**（credentials 默认档；
    kind=claude-cli 时是免 Key 的本机命令行订阅通道）> config [llm]/[ta]。
    profile 只在填了 api_key 时才覆盖密钥（未填回退 config）。

    user（2026-11-02 多账号）：传入普通用户时**只用该用户自己的 profile**
    ——没有就抛 OWN_KEY_REQUIRED 标记错误（绝不回退到管理员/config 的共享
    密钥）；claude-cli 是本机命令行订阅（机主身份），对普通用户拒绝。
    user 为 None 或管理员 → 原全局行为不变（系统消费方：推送/汇总/TA 等）。

    返回 {kind, base_url, model, api_key, source}。api 通道三处都无密钥
    → RuntimeError；claude-cli 通道不需要密钥。
    """
    if user is not None and not user.get("is_admin"):
        from .credentials import get_llm_profile
        try:
            p = get_llm_profile(None, user=user)
        except Exception:  # noqa: BLE001 — 凭证库读不了按未配置处理
            p = None
        if not p or not p.get("api_key") or not p.get("base_url"):
            raise RuntimeError(OWN_KEY_REQUIRED_MSG)
        if p.get("kind", "api") == "claude-cli":
            raise RuntimeError("订阅通道（Claude 命令行）仅管理员可用，请填写自己的 API Key")
        resolved = model or p.get("model") or "mimo-v2.6-flash"
        return {"kind": "api", "base_url": p["base_url"], "model": resolved,
                "api_key": p["api_key"], "source": "user.credentials.profile"}
    key, src = _read_llm_key(cfg)
    llm_cfg = cfg.get("llm", {}) or {}
    ta_cfg = cfg.get("ta", {}) or {}
    url = llm_cfg.get("backend_url") or ta_cfg.get(
        "backend_url", _GENERIC_FALLBACK)
    resolved = (model or llm_cfg.get("deep_model")
                or ta_cfg.get("deep_model", "mimo-v2.6-flash"))
    try:
        from .credentials import get_llm_profile
        p = get_llm_profile(None)
    except Exception:  # noqa: BLE001 — 凭证库读不了不挡通道（回退 config）
        p = None
    if isinstance(p, dict) and p.get("kind", "api") == "claude-cli":
        return {"kind": "claude-cli", "base_url": "",
                "model": p.get("model") or "", "api_key": "",
                "source": "credentials.profile"}
    if isinstance(p, dict) and p.get("base_url"):
        url = p["base_url"]
        if p.get("api_key"):
            key = p["api_key"]
            src = "credentials.profile"
        if model is None and p.get("model"):
            resolved = p["model"]
    if not key:
        raise RuntimeError(
            "未找到 LLM 密钥（前端凭证 profile / config [llm].api_key / "
            "TradingAgents .env 均无）")
    return {"kind": "api", "base_url": url, "model": resolved,
            "api_key": key, "source": src}


def resolve_api_channel(cfg: dict, model: str | None = None):
    """只解析 API 通道：跳过 claude-cli 默认 profile，回落 config [llm]/[ta]。

    返回 (base_url, model, api_key, source)。供只认 HTTP 网关的 TA 框架
    订阅档下回退使用；profile 与 config 都无钥匙 → RuntimeError。
    """
    ch = resolve_channel(cfg, model)
    if ch["kind"] == "api":
        return ch["base_url"], ch["model"], ch["api_key"], ch["source"]
    # 默认档是订阅：忽略 profile，重新走 config
    key, src = _read_llm_key(cfg)
    llm_cfg = cfg.get("llm", {}) or {}
    ta_cfg = cfg.get("ta", {}) or {}
    url = llm_cfg.get("backend_url") or ta_cfg.get(
        "backend_url", _GENERIC_FALLBACK)
    resolved = (model or llm_cfg.get("deep_model")
                or ta_cfg.get("deep_model", "mimo-v2.6-flash"))
    if not key:
        raise RuntimeError(
            "订阅通道不支持 TA，且未找到 [ta]/[llm] API 钥匙")
    return url, resolved, key, src


def resolve_llm(cfg: dict, model: str | None = None):
    """旧名保留：语义=API 通道（TA 与存量直接拼 HTTP 的调用点使用）。"""
    return resolve_api_channel(cfg, model)


def call_llm(cfg: dict, messages: list[dict], *, timeout: int | None = None,
             model: str | None = None, max_tokens: int = 16000,
             temperature: float = 0.3,
             retry_waits: tuple | None = None,
             user: dict | None = None) -> str:
    """调 OpenAI 兼容接口（通道解析见 resolve_channel：前端 profile 优先）。

    user：传普通用户时只用其自己的密钥档（多账号 2026-11-02），系统消费方
    （推送/汇总/TA 等）不传 = 原全局行为。

    429/5xx/网络抖动自动退避重试（MiMo 突发限流是分钟级窗口，2026-10-11 实测
    连续 429 后约 1 分钟恢复；汇总失败重跑代价大——帖子已抓、只差这一步）：
    最多 4 次尝试，间隔 30s/60s/120s，响应带 Retry-After 时以其为准；
    4xx（400/401 等）不重试直接抛。

    timeout/model 显式传入时覆盖 config 解析值（timeout 缺省走
    [llm].digest_timeout_sec 默认 900；对话等短超时场景由调用方传入）。

    retry_waits：覆盖默认退避序列 (30, 60, 120)（尝试次数 = len+1）。
    None = 保持默认——zsxq 日报/本地资料摘要等长输出场景照旧（汇总失败重跑
    代价大，值得等）；对话等短超时场景由调用方传短序列（如 (5.0, 10.0)），
    防止前端 60s 已 abort、服务端线程还按日报节奏睡 30/60/120 续杯 429。
    """
    import httpx
    import time as _time

    if timeout is None:
        llm_cfg = cfg.get("llm", {}) or {}
        timeout = int(llm_cfg.get("digest_timeout_sec", 900))

    channel = resolve_channel(cfg, model, user=user)
    if channel["kind"] == "claude-cli":
        # CLI 模式：即时失败不退避（digest 层自有 ATTEMPTS，chat 单次报错）
        return call_cli(messages, timeout=timeout)
    backend_url = channel["base_url"]
    model = channel["model"]
    key = channel["api_key"]

    url = backend_url.rstrip("/") + "/chat/completions"
    # 推理模型的 reasoning token 也占 max_tokens，日报输出长，预算给足
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    waits = tuple(retry_waits) if retry_waits is not None else (30.0, 60.0, 120.0)
    last_err, wait = None, (waits[0] if waits else 0)
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        for attempt in range(len(waits) + 1):
            if attempt:
                _time.sleep(wait)
            try:
                resp = client.post(url, json=payload, headers=headers)
            except httpx.TransportError as e:  # 连接重置/超时等网络抖动
                last_err = f"网络错误：{e}"
                wait = (waits[min(attempt, len(waits) - 1)] if waits else 0)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}：{resp.text[:150]}"
                wait = (waits[min(attempt, len(waits) - 1)] if waits else 0)
                ra = resp.headers.get("Retry-After")
                if ra:
                    try:
                        wait = max(float(ra), 5.0)
                    except ValueError:
                        pass
                continue
            if resp.status_code == 400 and "temperature" in resp.text \
                    and "temperature" in payload:
                # 火山 ark 推理模型只允许 temperature=1（2026-11-10 实测：
                # 网关当日起强制，此前 0.3 可过）；去掉该参数立即重试不睡觉。
                # 不修会炸 0:00/12:00 定时汇总与回填——全模块共用此通道。
                payload.pop("temperature", None)
                wait = 0
                continue
            resp.raise_for_status()
            body = resp.json()
            break
        else:
            raise RuntimeError(f"LLM 限流/故障重试 {len(waits) + 1} 次仍失败：{last_err}")
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"LLM 返回结构异常：{json.dumps(body, ensure_ascii=False)[:300]}")


def stream_llm(cfg: dict, messages: list[dict], *, timeout: int | None = None,
               model: str | None = None, max_tokens: int = 2000,
               temperature: float = 0.3,
               retry_waits: tuple | None = None,
               user: dict | None = None):
    """流式调 OpenAI 兼容接口，yield 文本增量（对话框逐字流出，2026-11-01）。

    - 首个增量产出**前**：429/5xx/网络抖动按 retry_waits 退避重试（语义同 call_llm）；
    - 首个增量产出**后**：任何错误抛 RuntimeError（已流出内容无法收回，不重试）；
    - claude-cli 通道不支持流式 → 整段取回后单次 yield（前端体验同普通返回）；
    - 空流（[DONE] 直达）= 正常返回零增量，调用方按空答案降级。
    """
    import httpx
    import time as _time

    if timeout is None:
        timeout = 60

    channel = resolve_channel(cfg, model, user=user)
    if channel["kind"] == "claude-cli":
        text = call_cli(messages, timeout=timeout)
        if text:
            yield text
        return
    backend_url = channel["base_url"]
    model = channel["model"]
    key = channel["api_key"]

    url = backend_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True,
               "temperature": temperature, "max_tokens": max_tokens}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    waits = tuple(retry_waits) if retry_waits is not None else (30.0, 60.0, 120.0)

    last_err, wait = None, (waits[0] if waits else 0)
    for attempt in range(len(waits) + 1):
        if attempt:
            _time.sleep(wait)
        emitted = False
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
                with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_err = (f"HTTP {resp.status_code}："
                                    f"{(resp.read() or b'').decode('utf-8', 'replace')[:150]}")
                        wait = waits[min(attempt, len(waits) - 1)] if waits else 0
                        ra = resp.headers.get("Retry-After")
                        if ra:
                            try:
                                wait = max(float(ra), 5.0)
                            except ValueError:
                                pass
                        continue
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content")
                        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                            continue  # 心跳行/结构漂移行跳过，不中断流
                        if delta:
                            emitted = True
                            yield delta
                    return  # 正常流结束
        except httpx.TransportError as e:
            if emitted:
                raise RuntimeError(f"流式中断（已输出部分内容）：{e}") from e
            last_err = f"网络错误：{e}"
            wait = waits[min(attempt, len(waits) - 1)] if waits else 0
            continue
        except Exception as e:  # noqa: BLE001 — raise_for_status 等
            if emitted:
                raise RuntimeError(f"流式中断（已输出部分内容）：{e}") from e
            last_err = str(e)
            wait = waits[min(attempt, len(waits) - 1)] if waits else 0
            continue
    raise RuntimeError(f"LLM 限流/故障重试 {len(waits) + 1} 次仍失败：{last_err}")

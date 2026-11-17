"""结构化错误：AI 可读、人可照做的错误对象。"""

from __future__ import annotations

# 错误码清单（保持稳定，AI 侧可能据此分支处理）
CONNECTION_REFUSED = "CONNECTION_REFUSED"
TIMEOUT = "TIMEOUT"
CLI_MISSING = "CLI_MISSING"
AUTH_FAILED = "AUTH_FAILED"
FILE_MISSING = "FILE_MISSING"
PARSE_ERROR = "PARSE_ERROR"
DB_ERROR = "DB_ERROR"
EMPTY_TABLE = "EMPTY_TABLE"
NOT_FOUND = "NOT_FOUND"
NOT_CONFIGURED = "NOT_CONFIGURED"
DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
UPSTREAM_ERROR = "UPSTREAM_ERROR"


def make_error(code: str, message: str, hint: str | None = None, retryable: bool = False) -> dict:
    """生成统一错误对象。hint 是给人看的排错步骤，应原样转述给用户。"""
    err = {"code": code, "message": message, "retryable": retryable}
    if hint:
        err["hint"] = hint
    return err

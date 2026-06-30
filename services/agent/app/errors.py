"""统一错误: 映射到 packages/contracts 的 ApiError 固定 code (Codex-10)。"""
from __future__ import annotations
from typing import Any


class ApiError(Exception):
    """携带契约 code + HTTP 状态码的错误; 由 main 的 exception_handler 渲染。"""

    def __init__(self, status_code: int, code: str, message: str,
                 details: dict[str, Any] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

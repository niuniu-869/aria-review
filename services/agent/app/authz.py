"""授权依赖：资源作用域校验（Phase B Round 5）。

与 auth.py（认证：你是谁）分离——authz 负责授权（这个资源归不归你）。
向后兼容：owner_id 为空（迁移前的存量项目）视为公共，放行；非空则强制归属校验。
迁移回填 + owner_id NOT NULL 后，此兼容分支自然失效，隔离全量生效。
"""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import COOKIE_NAME, get_current_user, hash_token, _origin_of, _trusted_origins
from .db import get_session
from .errors import ApiError
from .models import Project, User
from .repositories import project as project_repo
from .repositories import session as session_repo


def _path_project_id(request: Request) -> int:
    """从路径参数取 project id（兼容 {pid} 与 {project_id} 两种命名）。"""
    raw = request.path_params.get("pid") or request.path_params.get("project_id")
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ApiError(404, "PROJECT_NOT_FOUND", "项目不存在")


async def get_owned_project(
    request: Request,
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
) -> Project:
    """校验路径 project 归属当前用户；不存在或不属于则 404（不泄露存在性）。"""
    pid = _path_project_id(request)
    proj = await project_repo.get_project(s, pid)
    if proj is None or (proj.owner_id is not None and proj.owner_id != user.id):
        raise ApiError(404, "PROJECT_NOT_FOUND", "项目不存在")
    return proj


# ---------------------------------------------------------------------------
# 全局守卫：一处覆盖所有路由的「必须登录 + project 归属」，避免逐路由挂载遗漏
# （§8.2 头号越权风险）。豁免公开路径；测试经 dependency_overrides[global_guard] 放行。
# ---------------------------------------------------------------------------

_EXEMPT_EXACT = frozenset({"/healthz", "/public/stats", "/docs", "/openapi.json", "/redoc"})
_EXEMPT_PREFIXES = ("/auth/", "/ai/assets/")


async def global_guard(request: Request, s: AsyncSession = Depends(get_session)) -> None:
    """所有路由的统一守卫：认证 + （路径含 project id 时）owner 归属校验。"""
    if request.method == "OPTIONS":
        return  # CORS 预检放行
    path = request.url.path
    if path in _EXEMPT_EXACT or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return
    # CSRF 第二道防线（F-14）：非安全方法校验 Origin/Referer 属可信来源。
    # 与 auth.require_csrf 同语义：无 Origin/Referer（curl/脚本/同源浏览器 POST）放行，
    # 依赖 SameSite=Lax cookie；仅当来源存在且不在白名单才拒。
    if request.method not in ("GET", "HEAD"):
        allowed = _trusted_origins()
        if allowed:
            origin = request.headers.get("origin")
            src = origin or _origin_of(request.headers.get("referer") or "")
            if src is not None and src not in allowed:
                raise ApiError(403, "CSRF_REJECTED", "请求来源不被信任")
    # 认证
    tok = request.cookies.get(COOKIE_NAME)
    user = await session_repo.resolve_user(s, hash_token(tok)) if tok else None
    if user is None or user.status != "active":
        raise ApiError(401, "UNAUTHENTICATED", "未登录")
    request.state.user = user
    # owner 隔离（路径含 project id 时）；owner_id 为空的存量项目向后兼容放行。
    raw = request.path_params.get("pid") or request.path_params.get("project_id")
    if raw is not None:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            return
        proj = await project_repo.get_project(s, pid)
        if proj is None or (proj.owner_id is not None and proj.owner_id != user.id):
            raise ApiError(404, "PROJECT_NOT_FOUND", "项目不存在")

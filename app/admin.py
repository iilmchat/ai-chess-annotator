"""Админ-панель на `starlette-admin` (T-028, частично).

Итерация 16 разворачивает **базовый** control plane:

* `/admin` монтируется в `create_app()` и работает с тем же `AsyncEngine`,
  что и API (через `SessionLocal`);
* доступ защищён: заголовок ``X-Admin-Token`` (тот же `ADMIN_API_KEY`, что
  у `/v1/ingest`) **или** cookie-сессия после формы входа
  (`ADMIN_USERNAME` / `ADMIN_PASSWORD`, паролем также принимается токен);
* подключены read-only views для `annotated_games`, `commentaries` и
  `edits`.

Чего пока нет (следующие шаги T-028/T-035): ролей `user/admin/auditor`,
мягкого удаления RAG, серверных сессий в Redis, аудита мутаций и
RBAC-фильтров. Поэтому все views намеренно read-only: любая запись должна
появиться вместе с аудитом, а не до него.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette_admin import CustomView, route
from starlette_admin.auth import AdminUser, AuthProvider
from starlette_admin.contrib.sqla import Admin, ModelView
from starlette_admin.exceptions import FormValidationError, LoginFailed

from app.audit import (
    actor_from_request,
    append_audit,
    hash_text,
    verify_audit_chain,
)
from app.config import Settings
from app.db.models import AnnotatedGame, AuditLog, Commentary, Edit, Prompt
from app.db.session import SessionLocal
from app.prompts import reset_prompt_cache
from app.prompts.pubsub import publish_prompt_update

__all__ = [
    "AdminAuthGuard",
    "AdminAuthProvider",
    "AdminPathNormalizer",
    "AuditLogAdmin",
    "AuditVerifyView",
    "PromptAdmin",
    "create_admin",
]

logger = structlog.get_logger(__name__)


def _same_secret(left: str, right: str) -> bool:
    """Constant-time сравнение строк (в т.ч. не-ASCII — через bytes)."""
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class AdminAuthProvider(AuthProvider):
    """Токен в заголовке + простая форма логина/пароля.

    `starlette-admin` сам рисует страницу `/admin/login` и обрабатывает
    cookie-сессию; здесь только проверка учётных данных.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            login_path="/login",
            logout_path="/logout",
            allow_routes=["logout"],
        )
        self._settings = settings

    async def authenticate(self, request: Request) -> AdminUser | None:
        """Принять `X-Admin-Token` или активную cookie-сессию."""
        token = request.headers.get("x-admin-token")
        expected = self._settings.admin_api_key
        if token is not None and expected and _same_secret(token, expected):
            return AdminUser(username=self._settings.admin_username)

        session_user = request.session.get("admin_user")
        if session_user == self._settings.admin_username:
            return AdminUser(username=self._settings.admin_username)
        return None

    async def login(
        self,
        username: str,
        password: str,
        remember_me: bool,
        request: Request,
    ) -> Response | None:
        """Проверить логин/пароль и положить пользователя в сессию.

        Паролем принимается `ADMIN_PASSWORD`, а если он не задан — сам
        `ADMIN_API_KEY` (чтобы не заводить второй секрет на dev-машине).
        """
        cfg = self._settings
        if not cfg.admin_password and not cfg.admin_api_key:
            raise LoginFailed(
                "Админ-панель не настроена: задайте ADMIN_API_KEY или ADMIN_PASSWORD"
            )

        valid_user = _same_secret(username, cfg.admin_username)
        valid_password = False
        if cfg.admin_password and _same_secret(password, cfg.admin_password):
            valid_password = True
        if cfg.admin_api_key and _same_secret(password, cfg.admin_api_key):
            valid_password = True

        if valid_user and valid_password:
            request.session["admin_user"] = cfg.admin_username
            logger.info("admin_login", username=username)
            return None
        logger.warning("admin_login_failed", username=username)
        raise LoginFailed("Неверный логин или пароль")

    async def logout(self, request: Request) -> Response | None:
        request.session.clear()
        return None


class AdminPathNormalizer:
    """Приводить `/admin` к `/admin/` до маршрутизации.

    `Mount("/admin", ...)` в Starlette срабатывает на `/admin/...`, но не
    на точный `/admin`; без нормализации такой запрос проваливается в SPA
    и даёт 404. Middleware не трогает остальные пути.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/admin":
            scope = dict(scope)
            scope["path"] = "/admin/"
            scope["raw_path"] = b"/admin/"
        await self.app(scope, receive, send)


class AdminAuthGuard(BaseHTTPMiddleware):
    """Вернуть 401/403 вместо редиректа на логин для API-клиентов.

    `starlette-admin` по умолчанию отвечает 303 на `/admin/login`. Для
    smoke/тестов и для защиты от перебора нужен явный статус: без
    учётных данных — 401, с неверным `X-Admin-Token` — 403.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings
        self._login_path = "/admin/login"
        self._logout_path = "/admin/logout"
        self._static_prefix = "/admin/static/"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if (
            path in {self._login_path, self._logout_path}
            or path.startswith(self._static_prefix)
        ):
            return await call_next(request)

        token = request.headers.get("x-admin-token")
        if token is not None:
            expected = self._settings.admin_api_key
            if expected and _same_secret(token, expected):
                return await call_next(request)
            return JSONResponse(
                {"detail": "Invalid admin token", "code": "admin_forbidden"},
                status_code=403,
            )

        session = request.scope.get("session")
        if isinstance(session, dict) and session.get("admin_user") == (
            self._settings.admin_username
        ):
            return await call_next(request)

        return JSONResponse(
            {"detail": "Admin authentication required", "code": "admin_unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )


class _ReadOnlyModelView(ModelView):
    """Просмотр без создания/изменения/удаления (до появления аудита)."""

    page_size = 25

    def can_create(self, request: Request) -> bool:
        return False

    def can_edit(self, request: Request) -> bool:
        return False

    def can_delete(self, request: Request) -> bool:
        return False


class AnnotatedGameAdmin(_ReadOnlyModelView):
    """RAG-партии: просмотр источника, игроков, года и аннотации."""

    # `embedding` — `VECTOR(768)`, для которого у starlette-admin нет
    # автоматического конвертера; перечисляем поля явно, чтобы не
    # тащить вектор в UI.
    # starlette-admin принимает имена полей и сам конвертирует их в
    # BaseField; в аннотации базового класса стоит Sequence[BaseField].
    fields = (  # type: ignore[assignment]
        "id",
        "source",
        "white",
        "black",
        "event",
        "year",
        "annotation_text",
        "pgn",
    )
    exclude_fields_from_list = ("pgn", "annotation_text")
    searchable_fields = ("source", "white", "black", "event")
    sortable_fields = ("id", "source", "year")


class CommentaryAdmin(_ReadOnlyModelView):
    """Сгенерированные комментарии: уровень, модель, токены, флаги."""

    # Явный список без `game`/`edits`: связанные views в этой итерации не
    # регистрируются, а `RelationField` требует существующий key.
    fields = (  # type: ignore[assignment]
        "id",
        "game_id",
        "level",
        "text",
        "model",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "validated",
        "human_edited",
        "created_at",
    )
    exclude_fields_from_list = ("text",)
    searchable_fields = ("model",)
    sortable_fields = ("id", "created_at", "level", "model")


class EditAdmin(_ReadOnlyModelView):
    """История ручных правок — датасет для дообучения."""

    fields = (  # type: ignore[assignment]
        "id",
        "commentary_id",
        "original_text",
        "edited_text",
        "editor_id",
        "created_at",
    )
    exclude_fields_from_list = ("original_text", "edited_text")
    searchable_fields = ()
    sortable_fields = ("id", "created_at", "commentary_id")


class PromptAdmin(ModelView):
    """Редактор системных промптов (T-029, итерация 17).

    Редактируется только `content`; `level` — ключ, `version` и
    `updated_at` обновляются сервером. После коммита кэш сбрасывается, и
    следующий LLM-вызов читает новую версию из БД без рестарта.
    """

    page_size = 10
    fields = (  # type: ignore[assignment]
        "id",
        "level",
        "content",
        "version",
        "updated_at",
    )
    exclude_fields_from_list = ("content",)
    exclude_fields_from_edit = ("id", "level", "version", "updated_at")
    exclude_fields_from_create = ("id", "version", "updated_at")
    searchable_fields = ("level",)
    sortable_fields = ("id", "level", "version", "updated_at")

    def can_create(self, request: Request) -> bool:
        return False

    def can_delete(self, request: Request) -> bool:
        return False

    async def validate(
        self, request: Request, data: dict[str, Any]
    ) -> None:
        if not str(data.get("content") or "").strip():
            raise FormValidationError(
                {"content": "Промпт не может быть пустым"}
            )

    async def edit(self, request: Request, pk: Any, data: dict[str, Any]) -> Any:
        # Старый контент нужен для аудита: hook before_edit уже видит новое
        # значение, потому что starlette-admin сначала заполняет объект.
        old = await self.find_by_pk(request, pk)
        if old is not None:
            request.state.prompt_old_hash = hash_text(old.content)
            request.state.prompt_old_version = int(old.version or 0)
        return await super().edit(request, pk, data)

    async def before_edit(
        self, request: Request, data: dict[str, Any], obj: Any
    ) -> None:
        obj.version = int(getattr(obj, "version", 0) or 0) + 1
        obj.updated_at = datetime.now(UTC)

    async def after_edit_committed(self, request: Request, obj: Any) -> None:
        raw_level = getattr(obj, "level", None)
        level = str(raw_level) if raw_level is not None else None
        # Локальная инвалидация нужна всегда; Pub/Sub — для других воркеров.
        reset_prompt_cache(level)
        state = getattr(getattr(request, "app", None), "state", None)
        redis_client = getattr(state, "redis", None)
        if redis_client is not None:
            await publish_prompt_update(redis_client, level)
        await _write_prompt_audit(request, obj, level)
        logger.info(
            "prompt_updated",
            level=level,
            version=getattr(obj, "version", None),
        )


async def _write_prompt_audit(
    request: Request, obj: Any, level: str | None
) -> None:
    """Аудит правки промпта после commit (best-effort, отдельная сессия)."""
    content = str(getattr(obj, "content", ""))
    try:
        async with SessionLocal() as session:
            await append_audit(
                session,
                actor=actor_from_request(request),
                action="prompt.update",
                target_id=str(getattr(obj, "id", "")) or None,
                details={
                    "level": level,
                    "old_version": getattr(
                        request.state, "prompt_old_version", None
                    ),
                    "new_version": getattr(obj, "version", None),
                    "old_content_sha256": getattr(
                        request.state, "prompt_old_hash", None
                    ),
                    "new_content_sha256": hash_text(content),
                    "new_len": len(content),
                },
            )
            await session.commit()
    except Exception as exc:
        logger.error("audit_prompt_write_failed", error=str(exc))


class AuditLogAdmin(_ReadOnlyModelView):
    """Read-only журнал аудита (T-035)."""

    fields = (  # type: ignore[assignment]
        "id",
        "timestamp",
        "actor",
        "action",
        "target_id",
        "details",
        "prev_hash",
        "current_hash",
    )
    exclude_fields_from_list = ("details", "prev_hash", "current_hash")
    searchable_fields = ("actor", "action", "target_id")
    sortable_fields = ("id", "timestamp", "actor", "action")


class AuditVerifyView(CustomView):
    """Страница проверки hash-chain с кнопкой повторной проверки."""

    menu_label = "Проверить целостность"
    icon = "fa fa-shield-halved"
    path = "/audit/verify"
    route_name = "audit-verify"

    @route("")
    async def index(self, request: Request) -> Response:
        async with SessionLocal() as session:
            result = await verify_audit_chain(session)
        templates = self.templates
        if templates is None:  # pragma: no cover - выставляется при mount
            raise RuntimeError("templates не инициализированы")
        return templates.TemplateResponse(
            request=request,
            name="audit_verify.html",
            context={
                "title": "Проверка целостности аудита",
                "ok": result.ok,
                "checked": result.checked,
                "errors": result.errors,
            },
        )


def _session_secret(settings: Settings) -> str:
    return settings.admin_session_secret or settings.jwt_secret


def create_admin(settings: Settings) -> Admin:
    """Собрать админку под текущие настройки (не монтирует её)."""
    admin = Admin(
        session_provider=SessionLocal,
        title="ChessAnnotator Admin",
        base_url="/admin",
        templates_dir=str(Path(__file__).resolve().parent / "templates"),
        auth_provider=AdminAuthProvider(settings),
        secret_key=_session_secret(settings),
        middlewares=[
            Middleware(
                SessionMiddleware,
                secret_key=_session_secret(settings),
                session_cookie="admin_session",
                max_age=settings.admin_session_ttl_min * 60,
                same_site="lax",
                https_only=settings.app_env not in {"dev", "test", "local"},
            ),
            Middleware(AdminAuthGuard, settings=settings),
        ],
    )
    admin.add_view(AnnotatedGameAdmin(AnnotatedGame, icon="fa fa-database"))
    admin.add_view(CommentaryAdmin(Commentary, icon="fa fa-comment"))
    admin.add_view(EditAdmin(Edit, icon="fa fa-pen"))
    admin.add_view(PromptAdmin(Prompt, icon="fa fa-file-text"))
    admin.add_view(AuditLogAdmin(AuditLog, icon="fa fa-clipboard-list"))
    admin.add_view(AuditVerifyView())
    return admin

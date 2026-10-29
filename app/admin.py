"""Админ-панель на `starlette-admin` (T-028).

Control plane монтируется в `create_app()` и работает с тем же
`AsyncEngine`, что и API (через `SessionLocal`).

Способы аутентификации (T-028 финал):

* **JWT (основной путь)** — access-токен из `Authorization: Bearer <jwt>`
  или из cookie ``access_token``; пользователь загружается из БД, роль
  ``admin``/``auditor`` даёт доступ, ``user`` — нет; ``is_active=False`` —
  нет. JWT проверяется и в guard (статус-коды), и в провайдере
  (starlette-admin), причём guard кэширует результат в `request.state`,
  чтобы не делать второй запрос в БД.
* **``X-Admin-Token``** — legacy service-token (тот же `ADMIN_API_KEY`, что
  у `/v1/ingest`) для автоматизации/smoke-проверок.
* **Парольная форма** ``/admin/login`` (`ADMIN_USERNAME`/`ADMIN_PASSWORD`,
  паролем также принимается токен) — legacy fallback: cookie-сессия
  `admin_session` живёт `ADMIN_SESSION_TTL_MIN` минут.

Guard отдаёт JSON вместо редиректа: нет креденшелов — 401
``admin_unauthorized``, неверные/просроченные креды или роль ``user`` —
403 ``admin_forbidden``. Публичны только `/admin/login`, `/admin/logout`
и `/admin/static/*`.

RBAC-фильтров и аудита мутаций пока нет, поэтому все views read-only
(кроме редактора промптов): любая запись должна появиться вместе с
аудитом, а не до него.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
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
from app.auth.tokens import decode_token
from app.config import Settings
from app.db.models import (
    AdminRole,
    AnnotatedGame,
    AuditLog,
    Commentary,
    Edit,
    LlmEvaluation,
    Prompt,
    User,
)
from app.db.session import SessionLocal
from app.errors import AuthError
from app.prompts import reset_prompt_cache
from app.prompts.pubsub import publish_prompt_update

__all__ = [
    "AdminAuthGuard",
    "AdminAuthProvider",
    "AdminPathNormalizer",
    "AuditLogAdmin",
    "AuditVerifyView",
    "LlmEvaluationAdmin",
    "PromptAdmin",
    "create_admin",
]

logger = structlog.get_logger(__name__)


def _same_secret(left: str, right: str) -> bool:
    """Constant-time сравнение строк (в т.ч. не-ASCII — через bytes)."""
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


#: Роли, которым разрешён вход в админ-панель (T-028).
_ADMIN_ROLES: frozenset[AdminRole] = frozenset(
    {AdminRole.admin, AdminRole.auditor}
)

#: Атрибут `request.state`: guard кладёт сюда проверенную JWT-идентичность,
#: чтобы `AdminAuthProvider` не делал повторный запрос в БД.
_JWT_IDENTITY_STATE = "admin_jwt_identity"


@dataclass(frozen=True, slots=True)
class _JwtIdentity:
    """Пользователь, подтверждённый access-токеном."""

    email: str
    #: ``False`` — токен валиден, но роль (``user``) не даёт доступа в админку.
    allowed: bool


def _authorization_token(request: Request) -> str | None:
    """Bearer-токен из ``Authorization``.

    ``None`` — заголовка нет (дальше смотрим cookie/сессию); пустая
    строка — заголовок есть, но это не ``Bearer <token>`` (битые креды).
    """
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return ""
    return credentials.strip()


async def _jwt_identity(settings: Settings, token: str) -> _JwtIdentity | None:
    """Проверить access-токен и загрузить пользователя из БД.

    Общий код guard и провайдера, чтобы их решения не расходились.

    Возвращает ``None``, если токен битый/просрочен/не ``access``, а также
    если пользователя нет или он деактивирован (``is_active=False``). Роль
    проверяется здесь же, но результат отдаётся флагом ``allowed``: guard
    должен отличить «нет прав» (403) от «нет креденшелов» (401).
    """
    if not token:
        return None
    try:
        payload = decode_token(token, settings, expected_type="access")
    except AuthError:
        return None
    async with SessionLocal() as session:
        user = await session.get(User, payload.user_id)
        if user is None or not user.is_active:
            return None
        return _JwtIdentity(
            email=user.email, allowed=user.role in _ADMIN_ROLES
        )


async def _jwt_identity_from_request(
    settings: Settings, request: Request
) -> _JwtIdentity | None:
    """Идентичность из Bearer-заголовка или cookie ``access_token``."""
    token = _authorization_token(request)
    if token is None:
        token = request.cookies.get("access_token")
    if not token:
        return None
    return await _jwt_identity(settings, token)


def _admin_forbidden(detail: str) -> JSONResponse:
    """403 для неверных кредов/недостаточной роли (не редирект на логин)."""
    return JSONResponse(
        {"detail": detail, "code": "admin_forbidden"}, status_code=403
    )


class AdminAuthProvider(AuthProvider):
    """JWT (admin/auditor) + legacy service-token и форма логина/пароля.

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
        """Принять JWT, `X-Admin-Token` или активную cookie-сессию.

        Порядок: JWT (Bearer/cookie) → legacy service-token → legacy
        парольная сессия. Guard уже отклонил негодные JWT на защищённых
        путях, поэтому здесь достаточно вернуть ``AdminUser`` без
        повторного объяснения статуса.
        """
        settings = self._settings

        # Guard проверяет JWT первым и кладёт результат в request.state —
        # второй запрос в БД на том же запросе не нужен.
        cached = getattr(request.state, _JWT_IDENTITY_STATE, None)
        identity: _JwtIdentity | None
        if isinstance(cached, _JwtIdentity):
            identity = cached
        else:
            identity = await _jwt_identity_from_request(settings, request)
        if identity is not None:
            if identity.allowed:
                return AdminUser(username=identity.email)
            logger.warning(
                "admin_jwt_role_denied",
                username=identity.email,
                path=request.url.path,
            )
            return None

        # Legacy service-token: автоматизация/smoke (X-Admin-Token).
        token = request.headers.get("x-admin-token")
        expected = settings.admin_api_key
        if token is not None and expected and _same_secret(token, expected):
            return AdminUser(username=settings.admin_username)

        # Legacy fallback: cookie-сессия после парольной формы /admin/login.
        session_user = request.session.get("admin_user")
        if session_user == settings.admin_username:
            return AdminUser(username=settings.admin_username)
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
    smoke/тестов и для защиты от перебора нужен явный статус:

    * нет креденшелов — 401 ``admin_unauthorized``;
    * неверный `X-Admin-Token`, битый/просроченный JWT или роль ``user`` —
      403 ``admin_forbidden``;
    * валидный JWT роли ``admin``/``auditor``, legacy service-token или
      legacy сессия — доступ.
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

        # 1. Legacy service-token (X-Admin-Token): неверный — сразу 403.
        token = request.headers.get("x-admin-token")
        if token is not None:
            expected = self._settings.admin_api_key
            if expected and _same_secret(token, expected):
                return await call_next(request)
            return _admin_forbidden("Invalid admin token")

        # 2. JWT в Authorization: Bearer. Заголовок есть, но токен не
        #    принят — 403 (не путать с отсутствием креденшелов → 401).
        auth_token = _authorization_token(request)
        if auth_token is not None:
            identity = await _jwt_identity(self._settings, auth_token)
            if identity is not None and identity.allowed:
                setattr(request.state, _JWT_IDENTITY_STATE, identity)
                return await call_next(request)
            return _admin_forbidden(self._jwt_denied_detail(identity))

        # 3. JWT в cookie access_token — та же логика, что и для заголовка.
        cookie_token = request.cookies.get("access_token")
        if cookie_token:
            identity = await _jwt_identity(self._settings, cookie_token)
            if identity is not None and identity.allowed:
                setattr(request.state, _JWT_IDENTITY_STATE, identity)
                return await call_next(request)
            return _admin_forbidden(self._jwt_denied_detail(identity))

        # 4. Legacy fallback: cookie-сессия после парольной формы.
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

    @staticmethod
    def _jwt_denied_detail(identity: _JwtIdentity | None) -> str:
        """Разные детали для «битый токен» и «недостаточная роль»."""
        if identity is None:
            return "Invalid or expired access token"
        return "Admin role required"


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

    async def after_edit(self, request: Request, obj: Any) -> None:
        """Аудит в той же транзакции, что и правка (KI-033).

        `DBSessionMiddleware` коммитит сессию после успешного ответа; если
        `append_audit` упадёт, исключение превратит ответ в 4xx и
        middleware откатит и правку, и аудит.
        """
        raw_level = getattr(obj, "level", None)
        level = str(raw_level) if raw_level is not None else None
        content = str(getattr(obj, "content", ""))
        await append_audit(
            request.state.session,
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

    async def after_edit_committed(self, request: Request, obj: Any) -> None:
        raw_level = getattr(obj, "level", None)
        level = str(raw_level) if raw_level is not None else None
        # Локальная инвалидация нужна всегда; Pub/Sub — для других воркеров.
        reset_prompt_cache(level)
        state = getattr(getattr(request, "app", None), "state", None)
        redis_client = getattr(state, "redis", None)
        if redis_client is not None:
            await publish_prompt_update(redis_client, level)
        logger.info(
            "prompt_updated",
            level=level,
            version=getattr(obj, "version", None),
        )


class LlmEvaluationAdmin(_ReadOnlyModelView):
    """Таблица LLM-as-judge: модель, уровень, три шкалы (T-039)."""

    fields = (  # type: ignore[assignment]
        "id",
        "model",
        "level",
        "accuracy_score",
        "usefulness_score",
        "style_score",
        "created_at",
    )
    searchable_fields = ("model",)
    sortable_fields = (
        "id",
        "created_at",
        "level",
        "model",
        "accuracy_score",
        "usefulness_score",
        "style_score",
    )


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
    admin.add_view(
        LlmEvaluationAdmin(LlmEvaluation, icon="fa fa-chart-line")
    )
    admin.add_view(AuditVerifyView())
    return admin

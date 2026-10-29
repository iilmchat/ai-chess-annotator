"""T-110: security-валидаторы `Settings` (итерация 34).

Проверяем, что production не запускается с dev-секретами и что CORS не
смешивает `*` с `allow_credentials=True` (его приложение ставит всегда).
Dev/test-окружения должны оставаться запускаемыми с дефолтами.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

_GOOD_SECRET = "a" * 64


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "cors_origins": "https://chess.example.com",
        "admin_api_key": "prod-admin-token",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_dev_allows_default_secrets() -> None:
    cfg = Settings(_env_file=None)
    assert cfg.jwt_secret.startswith("dev_secret")
    assert cfg.default_language == "ru"


def test_prod_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(app_env="prod", jwt_secret="dev_secret_change_me")


def test_prod_rejects_placeholder_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(
            app_env="production",
            jwt_secret="change_me_in_prod_min_32_chars",
        )


def test_prod_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(app_env="prod", jwt_secret="x" * 31)


def test_prod_rejects_dev_admin_key() -> None:
    with pytest.raises(ValidationError, match="ADMIN_API_KEY"):
        _settings(
            app_env="prod",
            jwt_secret=_GOOD_SECRET,
            admin_api_key="dev-admin-token",
        )


def test_prod_accepts_real_secrets() -> None:
    cfg = _settings(
        app_env="prod",
        jwt_secret=_GOOD_SECRET,
        admin_api_key="real-admin-token-value",
    )
    assert cfg.app_env == "prod"


def test_prod_allows_empty_admin_key() -> None:
    """ADMIN_API_KEY опционален: без него admin-API просто закрыт."""
    cfg = _settings(app_env="prod", jwt_secret=_GOOD_SECRET, admin_api_key=None)
    assert cfg.admin_api_key is None


def test_cors_wildcard_is_rejected() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(_env_file=None, cors_origins="*")


def test_cors_explicit_list_is_allowed() -> None:
    cfg = Settings(
        _env_file=None,
        cors_origins="https://a.example.com, https://b.example.com",
    )
    assert cfg.cors_origin_list == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_default_language_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, default_language="de")

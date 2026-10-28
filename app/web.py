"""Раздача SPA и fallback на `index.html` для клиентских маршрутов.

`StaticFiles(html=True)` отдаёт `index.html` только для `/`, поэтому
прямой заход на `/history` или `/analysis/42` в production-Docker
возвращал 404. Здесь те же ассеты, но неизвестный путь без расширения
отдаёт SPA-шелл.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

_API_PREFIXES = ("v1/", "admin", "docs", "openapi", "redoc")


class SPAStaticFiles(StaticFiles):
    """StaticFiles + fallback на index.html для клиентских route."""

    async def get_response(
        self, path: str, scope: MutableMapping[str, Any]
    ) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            response = Response(status_code=404)
        if response.status_code != 404:
            return response
        normalized = path.lstrip("/").replace("\\", "/")
        if normalized.startswith(_API_PREFIXES) or Path(normalized).suffix:
            return response
        return await super().get_response("index.html", scope)


__all__ = ["SPAStaticFiles"]

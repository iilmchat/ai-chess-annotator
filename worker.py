"""Точка входа arq: `arq worker.WorkerSettings` (KI-053).

Логика воркера — в `app.worker` (пакет `app` ставится в wheel, а корневой
`worker.py` нужен только как имя модуля для CLI/Docker). Импорт
реэкспортирует класс и задачу, чтобы arq нашёл `WorkerSettings` по
короткому пути.
"""

from app.worker import WorkerSettings, run_export_job, shutdown, startup

__all__ = ["WorkerSettings", "run_export_job", "shutdown", "startup"]

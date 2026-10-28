# STEP-02 — `PATCH /v1/commentaries/{id}` (T-021b)

**Дата:** 2026-10-28
**Итерация:** 12
**Задача:** T-021b
**Статус:** ✅

## Что сделано

1. В `app/commentary/service.py` добавлен `edit_commentary(session,
   commentary_id, new_text, editor_id=None)`:
   - читает `commentaries.text` **до** изменения;
   - обновляет `text`, выставляет `human_edited = True`;
   - добавляет `Edit(commentary_id, original_text, edited_text, editor_id)`;
   - возвращает `EditedCommentary(commentary, edit_id)`.
2. В `app/api/v1/commentary.py` добавлен `PATCH /commentaries/{id}`:
   - тело `{"text": "..."}`, `min_length=1`, `max_length=20_000`;
   - пробельный текст отклоняется `field_validator`-ом (422);
   - id валидируется `Path(gt=0)`;
   - ответ: `commentary_id`, `game_id`, `level`, `text`, `human_edited`,
     `validated`, `model`, `edit_id`, `created_at`.
3. Ошибка `NotFoundError` → 404 через общий обработчик доменных ошибок.
4. Тесты `tests/integration/test_api_commentaries_patch.py` (4):
   - создание комментария → PATCH → `human_edited=True`, запись в `edits`
     с оригиналом и правкой;
   - повторный PATCH добавляет **вторую** запись (датасет не перезаписывается);
   - неизвестный id → 404 `not_found`;
   - пробельный текст → 422.

## Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Тесты PATCH | `pytest tests/integration/test_api_commentaries_patch.py -q` | `4 passed` (4.41 с) |
| Ответ API | test `test_patch_updates_text...` | `200`, `text=NEW_TEXT`, `human_edited=true`, `edit_id>0` |
| Запись в `edits` | SELECT в тесте | `[(ORIGINAL_TEXT, NEW_TEXT, None)]` |
| Повторная правка | test `test_second_patch...` | 2 записи: `ORIGINAL → NEW`, `NEW → третий текст` |
| 404 | `PATCH /v1/commentaries/999999999` | `{"code": "not_found"}` |
| 422 | `{"text": "   \n\t  "}` | `422` |

Пример ответа:

```json
{
  "commentary_id": 1,
  "game_id": 1,
  "level": "club",
  "text": "Белые разыграли ферзевый гамбит и получили перевес в центре.",
  "human_edited": true,
  "validated": true,
  "model": "test-model",
  "edit_id": 1,
  "created_at": "2026-10-28T18:55:00+00:00"
}
```

## Файлы

| Файл | Что |
| :--- | :--- |
| `app/commentary/service.py` | `EditedCommentary`, `edit_commentary`, экспорт |
| `app/commentary/__init__.py` | публичный экспорт `edit_commentary` |
| `app/api/v1/commentary.py` | `CommentaryPatchRequest/Response`, endpoint |
| `tests/integration/test_api_commentaries_patch.py` | 4 интеграционных теста |

## Проблемы и отклонения

- **T-021a не сделана:** панель комментария, SSE и inline-редактор в этом
  checkout отсутствуют (нет T-019/T-020) — KI-027. Поэтому T-021 остаётся
  `[~]`, хотя серверная часть (T-021b) закрыта.
- `editor_id` всегда `NULL`: аутентификации/JWT пока нет (KI-026-родственная
  тема, отдельного KI не заводим — пользователь появится в Фазе 5).
- Одновременные правки не блокируются (`SELECT ... FOR UPDATE` не
  используется): последний commit выигрывает, обе пары попадают в `edits`.
  Для датасета это допустимо; отдельного KI не заводим.

## Обоснование решений

- `edits` — append-only: каждая правка хранит собственный оригинал, поэтому
  повторное редактирование не теряет историю (важно для T-104).
- `validated` не сбрасывается: поле означает «ответ LLM прошёл валидатор»,
  а не «текст не менялся человеком»; факт правки выражен `human_edited`.
- `edit_commentary` живёт в сервисе, а не в эндпоинте: сценарий
  тестируется без HTTP и переиспользуется будущим UI.

## Что дальше

T-022: `POST /v1/chat` с контекстом анализа и валидатором.

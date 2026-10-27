# STEP-07 — Сборка фронтенда (T-019, частично)

**Дата:** 2026-10-28
**Итерация:** 1 — Bootstrap (T-001…T-006)
**Статус:** ✅ выполнен (потребовалась правка настроек pnpm 12)

---

## 1. Итоговый прогон

```
pnpm --dir web install     → Done in 2s using pnpm v12.14.0   [exit=0]
pnpm --dir web typecheck   → tsc --noEmit                     [exit=0]
pnpm --dir web build       → ✓ built in 1.91s                 [exit=0]
```

Артефакты:

```
web/dist/index.html                  0.32 kB
web/dist/assets/index-CjyBYGNx.js  224.26 kB │ gzip: 69.78 kB
```

## 2. Проблема: pnpm 12 блокирует postinstall esbuild

Первый `pnpm install` упал:

```
Error: ERR_PNPM_IGNORED_BUILDS
  × installing dependencies
  ╰─▶ Ignored build scripts: esbuild@0.21.5, esbuild@0.25.12
```

Причина: с pnpm 10+ lifecycle-скрипты зависимостей не выполняются без
явного разрешения, а pnpm 12 (**12.14.0** на машине) перестал читать
секцию `pnpm` из `package.json` и переименовал настройку
(`onlyBuiltDependencies` → `allowBuilds`).

Что пробовал и почему не сработало:

| Попытка | Результат |
| :--- | :--- |
| `"pnpm": {"onlyBuiltDependencies": ["esbuild"]}` в `web/package.json` | pnpm 12: «The "pnpm" field in package.json is no longer read», сборка всё ещё блокируется |
| `onlyBuiltDependencies` в `web/pnpm-workspace.yaml` | настройка удалена в pnpm 11 (`neverBuiltDependencies` и др. тоже) |
| **`allowBuilds: {esbuild: true}` в `web/pnpm-workspace.yaml`** | ✅ `esbuild postinstall: Done`, install зелёный |

Итог: добавлен `web/pnpm-workspace.yaml` (коммитится) — это теперь
каноническое место настроек pnpm, а не `.npmrc`/`package.json`.
Разрешение дано точечно одному `esbuild`, а не
`dangerouslyAllowAllBuilds`: политика supply-chain остаётся строгой
(в логе видно `Lockfile passes supply-chain policies`).

## 3. Что появилось в репозитории

| Файл | Зачем |
| :--- | :--- |
| `web/pnpm-workspace.yaml` | `allowBuilds: esbuild: true` (см. § 2) |
| `web/pnpm-lock.yaml` | 53 570 байт, фиксирует граф; CI кэширует по нему (`cache-dependency-path`) |
| `web/dist/` | артефакт сборки, в `.gitignore` (`web/dist/`) — не коммитится |

## 4. Версии фронтенда (фактические)

```
react 19.x, react-dom 19.x          @types/react 19.3.0
vite 6.4.5                          @vitejs/plugin-react 4.7.0
typescript 5.9.3                    vitest 2.1.9
node v24.21.0                       pnpm 12.14.0
```

## 5. Тесты фронтенда

```
pnpm --dir web test
No test files found, exiting with code 1
```

Тестов пока **0 файлов** — это ожидаемо: UI-компонентов ещё нет
(T-019 — только скелет). В CI шаг тестов идёт с `--passWithNoTests`
(`pnpm test -- --passWithNoTests` → exit 0), поэтому «зелёный» CI не
означает наличия фронтенд-покрытия. Зафиксировано как **KI-008**
(Medium/CI): шаг `pnpm test` — no-op до появления первого компонентного
теста в T-019/T-020.

## 6. Проверки DoD

| Проверка | Результат |
| :--- | :--- |
| `pnpm --dir web install` | ✅ exit 0 |
| `pnpm --dir web typecheck` | ✅ exit 0, без ошибок |
| `pnpm --dir web build` | ✅ exit 0, `web/dist/` создан |
| lockfile в репозитории | ✅ `web/pnpm-lock.yaml` |

## 7. Что дальше

Шаг 8 — пять документов (`RULES.md`, `PROMPT_CURRENT.md`, `RELEASES.md`,
`CHANGELOG.md`, `KNOWN_ISSUES.md`).

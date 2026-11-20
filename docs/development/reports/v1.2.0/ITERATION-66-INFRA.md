# Отчёт Infra-агента — Итерация 66 (T-104b / KI-060)

Дата: 2026-11-19/20. Выполнено на удалённом узле **DESKTOP-67ME6OH**
(RTX 3060 12 GB, Windows 11, CUDA) через reverse-SSH-туннель резерва
`62.109.1.230:22 → 127.0.0.1:2222`. Локальный хост — AndreyPC.

**Итог:** обучение 7B, экспорт GGUF и eval выполнены. Порог 7/10 **не взят**:
v4-7B = **3.20/10** против baseline v3 = **3.60/10** (Δ −0.40). KI-060
остаётся **Open** (CJK-критерий выполнен, порог качества — нет).

---

## (1) Файлы (+/-)

- **M** `.gitignore` — добавлено `*.gguf` (страховка от коммита GGUF вне `models/`).
- **A** `docs/development/reports/v1.2.0/ITERATION-66-INFRA.md` — этот отчёт.
- **A** `docs/development/reports/LLM_EVAL_V4_7B.md` — отчёт eval v4-7B.
- **A** `docs/development/reports/llm_eval_results_v4_7b.json` — сырые результаты.
- **A** `docs/development/reports/LLM_EVAL_V3_VS_V4_7B.md` — таблица дельт.
- **На узле (НЕ в git):** `C:\Projects\AI\chess-annotator-v4\` — клон репозитория,
  `.venv`, `models/base/Qwen2.5-7B-Instruct`, `models/lora-club-v4-7b`,
  `models/qwen2.5-7b-v4-clean-gguf/model.gguf`, логи `gpu_setup.log`,
  `train_v4.log`, `export_v4.log`, `eval_v4.log`.
- **Веса и датасеты не коммитились**: `models/`, `*.safetensors`, `*.gguf`,
  `data/*.jsonl` игнорируются (`git check-ignore` проверен).

## (2) Покрытие

Новых unit-тестов нет (код пайплайна не менялся). Верификация:
`pytest tests/unit/test_train_lora.py -q` локально — **82 passed**;
`train_lora.py --dry-run --dataset data/gold_dataset_v2.jsonl` на узле —
`556 записей, club=556, schema_version 2=556, DRY-RUN OK`.

## (3) Результаты

**Окружение узла (шаг 1):** Python 3.12.10, torch 2.14.1+cu126, torchvision
cu126, transformers 5.23.0, peft 0.22.0, bitsandbytes 0.50.2, accelerate,
datasets, tensorboard. `--check-env`: `cuda: да`, `device: NVIDIA GeForce
RTX 3060`, `bf16: да`. VRAM перед обучением — **1026 / 12288 MiB** (освобождена
выгрузкой LM Studio по согласованию с владельцем).

**Датасет (шаг 2):** из S3 `selectel:chess-annotator-data/gold_dataset_v2_full.jsonl`
→ `data/gold_dataset_v2.jsonl` на узле. 556 записей, `teacher_provider=deepseek`,
`schema_version=2`, `club`, **0 CJK**, ~1515 токенов/запись, максимум ~1884.
Новый датасет не создавался — использован существующий.

**Обучение (шаг 3):** 4-bit NF4 (QLoRA), `--model models/base/Qwen2.5-7B-Instruct`,
`--batch-size 1 --grad-accum 16`, `--max-length 2048`, `--lr 1e-4`,
`--epochs 1`, gradient checkpointing, `report_to=none`, seed 42.
`trainable=5,046,272` (0.07%). **7 оптимизационных шагов,
`train_loss=1.849`, `train_runtime=1127 с` (~18.8 мин), полный прогон
1190 с**, `TRAIN_RC 0`. Адаптер: `models/lora-club-v4-7b`
(`adapter_config.json`, `adapter_model.safetensors`, токенизатор).
Пик VRAM во время обучения не семплировался — **нет замера**; после загрузки
GGUF в GPU — 7778 / 12288 MiB.

**GGUF (шаг 4):** `export_to_gguf.py` — merge на CPU (~85 с), затем llama.cpp
`q8_0`: `models/qwen2.5-7b-v4-clean-gguf/model.gguf`, **8.1 ГБ**, экспорт
273 с (`EXPORT_RC 0`). Потребовался `pip install sentencepiece` в venv.
Файл скопирован в `~/.lmstudio/models/iiruchating/qwen-7b-v4-clean/` и
успешно загружен через `lms load qwen-7b-v4-clean --gpu max -c 4096 -y`
(`lms ps`: 8.10 GB, context 4096, Local).

**Eval (шаг 5):** замороженный набор `tests/fixtures/eval_games.json`,
5 партий, `club`, `--model-name qwen-7b-v4-clean`. Все 5 генераций выполнены
локальной моделью (`provider=lmstudio`, fallback в DeepSeek **отсутствует**).
Судья: `deepseek/deepseek-v4.1-flash-1017` через routerai, `max_tokens=2048`;
PostgreSQL/Redis и Stockfish 17.1 подняты на узле.

| Шкала | v3 (baseline) | v4-7B | Дельта |
| :--- | ---: | ---: | ---: |
| Факт. точность | 2.60 | 3.00 | +0.40 |
| Полезность | 3.40 | 3.00 | −0.40 |
| Стиль | 4.80 | 3.60 | −1.20 |
| **Средняя** | **3.60** | **3.20** | **−0.40** |
| Объяснения | 60.0% (3/5) | 40.0% (2/5) | −20 п.п. |
| CJK | 0.00% (0/5) | 0.00% (0/5) | 0.00 п.п. |

## (4) Smoke

- `nvidia-smi`: RTX 3060, driver 621.01, CUDA UMD 13.5.
- `--check-env`, `--dry-run`, smoke-запрос к `/v1/chat/completions`
  (ответ `Приветствие`) — успешно.
- 5× `/v1/analyze` → 200, 5× `/v1/commentary` → 200 (`provider=lmstudio`).
- `/v1/models` собственного llama-server отдаёт id GGUF; `compare_evals.py` — exit 0.
- LM Link: `lms link enable` выполнен, но статус **Offline** (см. §5).

## (5) Что не так

1. **LM Link не работает на узле.** `lms link status` → `Status: Offline`,
   `tsnet_up_failed: ... failed to get a store reader: Access is denied`
   (политика Windows/Tailscale). Поэтому подтвердить из AndreyPC запросом
   `curl` по LM Link **не удалось** — шаг 4 в этой части не закрыт.
2. Для eval пришлось собрать собственный `llama-server` (llama.cpp + CUDA,
   `C:\Projects\llama.cpp\build-cuda\bin\Release`) с `--api-key lm-studio`
   на `:8034`: встроенный API LM Studio требует токен, который недоступен
   (создаётся в UI и не показывается повторно). Модель в LM Studio загружалась
   (`lms ps`), но итоговый eval шёл через отдельный llama-server.
3. **CJK-дрейф частично сохраняется:** в логе eval один комментарий дал
   `commentary_cjk_stripped` (`cjk_chars=56`, `cjk_share=0.045`, удалено 2
   предложения) — итоговая метрика 0/5 достигается благодаря пост-фильтру,
   а не потому, что модель никогда не генерирует иероглифы (см. лог
   `commentary_cjk_stripped`).
4. Качество не улучшилось: 3.20 < baseline 4B 4.40 и << порога 7/10.
   Возможные причины: 1 эпоха на 556 записях при lr 1e-4 дала недообучение
   (loss 1.849); формат GGUF q8_0 не совпадает с bf16-обучением;
   шум судьи на 5 партиях.
5. Пик VRAM training не замерен (нетpite `nvidia-smi` семплирования).
6. Долгие SSH-сессии на резерве рвутся; один раз потребовалось убить
   зависшую сессию `sshd` (PID 2007332) для восстановления `:2222`.

## (6) Новые KI

Нет новых зарегистрированных KI. **KI-060 остаётся Open**: CJK-критерий
0/5 формально выполнен, но требуемый уровень не ниже 4.40/10 не достигнут
(3.20/10). KI-030 — Closed (T-134, 3B = 2.60/10). T-104b — in progress.

## (7) Обоснование

Использован существующий датасет из S3 (новый не готовился, по указанию
владельца); обучение — строго по `GPU_TRAINING_GUIDE.md` §6.2/§6.3:
1 эпоха, lr 1e-4, 4-bit, batch 1, grad-accum 16, gradient checkpointing —
чтобы не усилить CJK-дрейф.GGUF `q8_0` выбран, потому что `export_to_gguf.py`
поддерживает только `f16|bf16|q8_0` (`q4_k_m` требует отдельного
`llama-quantize`). Сравнение — на том же frozen set и том же судье, что и
baseline v3, с контролем отсутствия fallback. Веса и датасеты в git не
попадают: добавлен явный ignore `*.gguf`.

## Приложение. Ключевые пути и команды

- Узел: `ssh -J root@62.109.1.230 -i ~/.ssh/ssh-wg-desktop_ed25519 -p 2222`
  (ключ лежит на резерве `/root/.ssh/ssh-wg-desktop_ed25519`).
- Обучение: `python scripts/train_lora.py --model models/base/Qwen2.5-7B-Instruct
  --dataset data/gold_dataset_v2.jsonl --output-dir models/lora-club-v4-7b
  --load-in-4bit --batch-size 1 --grad-accum 16 --max-length 2048
  --lr 1e-4 --epochs 1 --gradient-checkpointing --report_to none --seed 42`
  (в задании были неверные флаги `--base_model`/`--output_dir` — это флаги
  `export_to_gguf.py`; у `train_lora.py` — `--model`/`--output-dir`).
- Экспорт: `python scripts/export_to_gguf.py --base_model models/base/Qwen2.5-7B-Instruct
  --adapter_path models/lora-club-v4-7b --output_dir models/qwen2.5-7b-v4-clean-gguf
  --llama_cpp_dir C:\Projects\llama.cpp --gguf_outtype q8_0 --device cpu`.
- Eval: `python scripts/evaluate_llm.py --games 5 --levels club
  --model-name qwen-7b-v4-clean --timeout-s 300` + `compare_evals.py`.

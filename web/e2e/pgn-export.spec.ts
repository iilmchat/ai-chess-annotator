import { readFile } from "node:fs/promises";

import { Chess } from "chess.js";

import { DEMO_PGN, positionsFromPgn } from "../src/lib/pgn";
import type { AnalyzeResponse, AnalyzedMove, Motif } from "../src/types";
import {
  expect,
  mockApi,
  REAL_BACKEND,
  registerAndLogin,
  test,
} from "./fixtures";

/**
 * T-36 (UX polish): скачивание аннотированного PGN и человекочитаемые
 * названия мотивов.
 *
 * Мок `POST /v1/analyze` строится из `DEMO_PGN` (20 полуходов) — столько же
 * позиций, сколько `HomePage` восстанавливает из PGN через chess.js, иначе
 * автовыбранный последний полуход не совпал бы с ходом разбора. На последний
 * ход кладутся все 9 типов мотивов: UI обязан показать RU-подписи и не
 * вывести ни одного snake_case-тега.
 *
 * Спека самодостаточна: все запросы API перехвачены (`mockApi`: /auth/me,
 * POST /v1/analyze, usage, games), реальный backend не нужен.
 */
const MOCK_ANALYSIS_ID = "42";

const MOTIF_TYPES = [
  "passed_pawn",
  "weak_square",
  "open_file",
  "open_diagonal",
  "king_safety",
  "fork",
  "pin",
  "mate_threat",
  "winning_capture",
];

const MOTIF_LABELS = [
  "Проходная пешка",
  "Слабое поле",
  "Открытая линия",
  "Открытая диагональ",
  "Безопасность короля",
  "Вилка",
  "Связка",
  "Угроза мата",
  "Выигрывающее взятие",
];

/** Разбор DEMO_PGN, у последнего хода — все 9 мотивов. */
function analysisWithMotifs(): AnalyzeResponse {
  const { initialFen, positions } = positionsFromPgn(DEMO_PGN);
  const lastPly = positions.length;
  const motifs: Motif[] = MOTIF_TYPES.map((type, index) => ({
    type,
    description: `Описание мотива ${index + 1}`,
    importance: 0.5,
  }));

  const moves: AnalyzedMove[] = positions.map((position) => ({
    ply: position.ply,
    san: position.san,
    uci: position.uci,
    fen: position.fen,
    eval_cp: 20 + position.ply,
    mate: null,
    best_move_san: position.san,
    delta_cp: 0,
    classification: "good",
    motifs: position.ply === lastPly ? motifs : [],
    recommended_move: null,
  }));

  return {
    analysis_id: MOCK_ANALYSIS_ID,
    status: "completed",
    meta: { white: "Svidler", black: "Shirov", eco: "C60" },
    initial_fen: initialFen,
    analysis_level: "club",
    moves_analyzed: positions.length,
    time_taken_ms: 1234,
    summary: { blunders: 0, mistakes: 1, inaccuracies: 0 },
    created_at: "2026-10-28T12:00:00Z",
    moves,
  };
}

test.beforeEach(async ({ page }) => {
  // Общие моки (auth/usage/games) + точечный override POST /v1/analyze:
  // Playwright отдаёт приоритет последнему зарегистрированному маршруту.
  await mockApi(page);
  if (!REAL_BACKEND) {
    await page.route(/^https?:\/\/[^/]+\/v1\/analyze$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json; charset=utf-8",
        body: JSON.stringify(analysisWithMotifs()),
      }),
    );
  }
});

test("PGN: скачивание аннотированного файла + RU-подписи мотивов", async ({
  page,
}) => {
  test.setTimeout(REAL_BACKEND ? 180_000 : 60_000);

  await registerAndLogin(page);

  // Информативный tooltip бейджа токенов: полные числа с разделителем по языку.
  const badge = page.getByTestId("usage-badge");
  await expect(badge).toBeVisible({ timeout: 15_000 });
  if (!REAL_BACKEND) {
    await expect(badge).toHaveAttribute(
      "title",
      /Израсходовано токенов LLM: 0 из 50\s000 \(дневной лимит\)/,
    );
  }

  await page.getByRole("button", { name: "Разобрать партию" }).click();
  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, {
    timeout: REAL_BACKEND ? 150_000 : 15_000,
  });

  // Последний полуход выбран автоматически → панель хода с мотивами.
  await expect(page.locator(".board-wrap")).toBeVisible();
  const motifList = page.locator(".motifs");
  await expect(motifList).toBeVisible();

  if (!REAL_BACKEND) {
    // Заголовок панели хода — 20-й полуход (Nbd7) с нашими мотивами.
    await expect(page.locator(".move-detail")).toContainText("Nbd7");
    for (const label of MOTIF_LABELS) {
      await expect(motifList).toContainText(label);
    }
    // Ни один известный тип не должен рендериться snake_case-тегом.
    await expect(motifList).not.toContainText(/_/);
  }

  // Кнопка «Скачать PGN» в шапке сводки: Playwright ловит download-событие.
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("download-pgn").click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toMatch(
    /^chessannotator-.+\.pgn$/,
  );
  const filePath = await download.path();
  if (!filePath) {
    throw new Error("Playwright не сохранил скачанный файл на диск");
  }
  const text = await readFile(filePath, "utf8");

  // Стандартная шапка PGN + результат, movetext с номерами ходов.
  expect(text.startsWith("[Event")).toBe(true);
  expect(text).toContain('[Annotator "ChessAnnotator"]');
  expect(text).toMatch(/\n\n1\. e4\b/);
  expect(text).toMatch(/(\*|1-0|0-1|1\/2-1\/2)\s*$/);
  // Комментарии к ходам — данные, без RU/EN-текста.
  expect(text).toMatch(/\{\+[\d.]+, [a-z_]+\}/);

  // Скачанный PGN обязан быть валидным: chess.js проигрывает его обратно.
  const replayed = new Chess();
  replayed.loadPgn(text);
  expect(replayed.history().length).toBeGreaterThan(0);
  expect(replayed.history()[0]).toBe("e4");
  if (!REAL_BACKEND) {
    expect(replayed.history()).toHaveLength(20);
  }
});

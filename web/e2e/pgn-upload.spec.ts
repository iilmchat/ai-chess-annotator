import { DEMO_PGN, positionsFromPgn } from "../src/lib/pgn";
import { MAX_PGN_FILE_BYTES } from "../src/lib/pgnFile";
import type { AnalyzeResponse, AnalyzedMove } from "../src/types";
import {
  expect,
  mockApi,
  REAL_BACKEND,
  registerAndLogin,
  test,
} from "./fixtures";

/**
 * T-071: загрузка PGN из файла в textarea на главной.
 *
 * Сценарий самодостаточен: `mockApi` закрывает /auth/me, usage и games,
 * а `POST /v1/analyze` перехвачен ниже и собирает ответ из загруженного
 * текста — так видно, что в анализ ушёл именно файл, а не DEMO_PGN.
 */
const MOCK_ANALYSIS_ID = "42";

/** Валидный PGN, отличный от DEMO_PGN: загрузка обязана заменить текст. */
const UPLOAD_PGN = `[Event "Uploaded"]
[Site "ChessAnnotator"]
[Date "2026.10.30"]
[White "Uploader"]
[Black "Reader"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0`;

/** Последний текст, который frontend отправил в POST /v1/analyze. */
let analyzedText: string | null = null;

/** Минимальный мок разбора под конкретный PGN (как в pgn-export.spec.ts). */
function analysisFor(pgn: string): AnalyzeResponse {
  const { initialFen, positions } = positionsFromPgn(pgn);
  const moves: AnalyzedMove[] = positions.map((position) => ({
    ply: position.ply,
    san: position.san,
    uci: position.uci,
    fen: position.fen,
    eval_cp: 20,
    mate: null,
    best_move_san: position.san,
    delta_cp: 0,
    classification: "good",
    motifs: [],
    recommended_move: null,
  }));

  return {
    analysis_id: MOCK_ANALYSIS_ID,
    status: "completed",
    meta: { white: "Uploader", black: "Reader", eco: "C60" },
    initial_fen: initialFen,
    analysis_level: "club",
    moves_analyzed: positions.length,
    time_taken_ms: 1000,
    summary: { blunders: 0, mistakes: 0, inaccuracies: 0 },
    created_at: "2026-10-30T12:00:00Z",
    moves,
  };
}

test.beforeEach(async ({ page }) => {
  analyzedText = null;
  // Общие моки (auth/usage/games) + точечный override POST /v1/analyze:
  // Playwright отдаёт приоритет последнему зарегистрированному маршруту.
  await mockApi(page);
  if (!REAL_BACKEND) {
    await page.route(/^https?:\/\/[^/]+\/v1\/analyze$/, async (route) => {
      const body = route.request().postDataJSON() as { pgn?: unknown } | null;
      analyzedText = typeof body?.pgn === "string" ? body.pgn : null;
      await route.fulfill({
        status: 200,
        contentType: "application/json; charset=utf-8",
        body: JSON.stringify(analysisFor(analyzedText ?? DEMO_PGN)),
      });
    });
  }
});

test("загрузка .pgn подставляет текст файла в редактор", async ({ page }) => {
  await registerAndLogin(page);

  const textarea = page.getByLabel("Вставьте PGN партии");
  // До загрузки в редакторе демо-партия.
  await expect(textarea).toHaveValue(DEMO_PGN);

  // Кнопка открывает диалог через ref; в тесте файл кладём напрямую.
  await page.getByTestId("pgn-upload").click();
  await page.setInputFiles('[data-testid="pgn-file-input"]', {
    name: "game.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(UPLOAD_PGN, "utf8"),
  });

  await expect(textarea).toHaveValue(UPLOAD_PGN);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("файл больше 1 МБ: ошибка и текст не меняется", async ({ page }) => {
  await registerAndLogin(page);

  const textarea = page.getByLabel("Вставьте PGN партии");
  await expect(textarea).toHaveValue(DEMO_PGN);

  await page.setInputFiles('[data-testid="pgn-file-input"]', {
    name: "huge.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.alloc(MAX_PGN_FILE_BYTES + 1, 0x61),
  });

  await expect(page.getByRole("alert")).toContainText("Файл больше 1 МБ");
  await expect(textarea).toHaveValue(DEMO_PGN);
});

test("файл .txt отклоняется с понятной ошибкой", async ({ page }) => {
  await registerAndLogin(page);

  await page.setInputFiles('[data-testid="pgn-file-input"]', {
    name: "game.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("1. e4 e5 1-0", "utf8"),
  });

  await expect(page.getByRole("alert")).toContainText(
    "Нужен файл с расширением .pgn",
  );
});

test("после загрузки анализируется текст файла", async ({ page }) => {
  test.setTimeout(REAL_BACKEND ? 180_000 : 60_000);

  await registerAndLogin(page);

  await page.setInputFiles('[data-testid="pgn-file-input"]', {
    name: "game.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(UPLOAD_PGN, "utf8"),
  });
  await expect(page.getByLabel("Вставьте PGN партии")).toHaveValue(
    UPLOAD_PGN,
  );

  await page.getByRole("button", { name: "Разобрать партию" }).click();
  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, {
    timeout: REAL_BACKEND ? 150_000 : 15_000,
  });

  if (!REAL_BACKEND) {
    // В API ушёл именно загруженный PGN (textarea-значение не подменялось).
    expect(analyzedText).toBe(UPLOAD_PGN);
  }
});

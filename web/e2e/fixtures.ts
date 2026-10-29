import { test as base, expect, type Page, type Route } from "@playwright/test";

import type {
  AnalyzeResponse,
  AuthTokens,
  AuthUser,
  EditedCommentary,
  GameListItem,
  UsageResponse,
} from "../src/types";

export { expect };

/**
 * `E2E_REAL_BACKEND=1` — не ставить page.route-моки и ходить в реальный
 * backend через Vite-proxy (VITE_API_PROXY). См. web/e2e/README.md.
 */
export const REAL_BACKEND = process.env.E2E_REAL_BACKEND === "1";

export const PASSWORD = "E2ePassword123";

/** id из мока POST /v1/analyze — по нему проверяем URL /analysis/:id. */
export const MOCK_ANALYSIS_ID = "42";
/** id партии из мока GET /v1/games. */
export const MOCK_HISTORY_GAME_ID = 77;
/** edit_id из мока PATCH /v1/commentaries/:id. */
export const MOCK_EDIT_ID = 55;
/** commentary_id из SSE-события done. */
export const MOCK_COMMENTARY_ID = 101;

const MOCK_COMMENTARY_DELTAS = [
  "Белые начинают с e4 и захватывают центр. ",
  "Чёрные отвечают e5 и уравнивают игру.",
];
export const MOCK_COMMENTARY_TEXT = MOCK_COMMENTARY_DELTAS.join("");
export const MOCK_EDITED_TEXT =
  "Комментарий после правки E2E: белые получили удобную игру в центре.";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const FEN_AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const FEN_AFTER_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2";
const FEN_AFTER_NF3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2";
const FEN_AFTER_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3";

/**
 * Уникальный email: важен для real-backend-режима (регистрация новой учётки).
 * Домен — `example.com`: `email-validator` в Pydantic отклоняет special-use
 * домены (`.local`, `.test`, `.invalid` и т.п.) с 422.
 */
export function uniqueEmail(prefix = "e2e"): string {
  const stamp = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${stamp}-${random}@example.com`;
}

// ---------------------------------------------------------------------------
// Мок-данные: повторяют контракты web/src/types.ts
// ---------------------------------------------------------------------------

function authUser(email: string): AuthUser {
  return {
    id: 1,
    email,
    level: "club",
    role: "user",
    is_active: true,
    created_at: "2026-10-28T10:00:00Z",
  };
}

function authTokens(): AuthTokens {
  // Refresh-токена здесь нет: backend отдаёт его только в HttpOnly cookie.
  return {
    access_token: "e2e-mock-access-token",
    token_type: "bearer",
    expires_in: 900,
  };
}

function usagePayload(): UsageResponse {
  // limit 50_000 → бейдж в шапке рендерится как "Free: 0/50k"
  // (UsageBadge.compact: 0 → "0", 50000 → "50k").
  return {
    enabled: true,
    tier: "free",
    used_tokens: 0,
    limit_tokens: 50_000,
    remaining_tokens: 50_000,
    reset_at: 1_800_000_000,
  };
}

export function mockAnalysis(analysisId: string): AnalyzeResponse {
  return {
    analysis_id: analysisId,
    status: "completed",
    meta: { white: "Svidler", black: "Shirov", eco: "C60" },
    initial_fen: START_FEN,
    analysis_level: "club",
    moves_analyzed: 4,
    time_taken_ms: 1234,
    summary: { blunders: 0, mistakes: 1, inaccuracies: 0 },
    created_at: "2026-10-28T12:00:00Z",
    moves: [
      {
        ply: 1,
        san: "e4",
        uci: "e2e4",
        fen: FEN_AFTER_E4,
        eval_cp: 30,
        mate: null,
        best_move_san: "e4",
        delta_cp: 0,
        classification: "good",
        motifs: [],
        recommended_move: null,
      },
      {
        ply: 2,
        san: "e5",
        uci: "e7e5",
        fen: FEN_AFTER_E5,
        eval_cp: 25,
        mate: null,
        best_move_san: "e5",
        delta_cp: 5,
        classification: "good",
        motifs: [
          { type: "center", description: "Борьба за центр", importance: 0.5 },
        ],
        recommended_move: null,
      },
      {
        ply: 3,
        san: "Nf3",
        uci: "g1f3",
        fen: FEN_AFTER_NF3,
        eval_cp: 35,
        mate: null,
        best_move_san: "Nf3",
        delta_cp: 0,
        classification: "best",
        motifs: [],
        recommended_move: null,
      },
      {
        ply: 4,
        san: "Nc6",
        uci: "b8c6",
        fen: FEN_AFTER_NC6,
        eval_cp: 30,
        mate: null,
        best_move_san: "Nc6",
        delta_cp: 5,
        classification: "good",
        motifs: [],
        recommended_move: null,
      },
    ],
  };
}

function gameList(): GameListItem[] {
  return [
    {
      id: MOCK_HISTORY_GAME_ID,
      white: "Svidler",
      black: "Shirov",
      event: "Demo",
      level: "club",
      created_at: "2026-10-28T12:00:00Z",
    },
  ];
}

// ---------------------------------------------------------------------------
// page.route-моки (mocked-режим, основной для DoD/CI)
// ---------------------------------------------------------------------------

function fulfillJson(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify(body),
  });
}

/** Тело SSE-ответа в формате web/src/api.ts (parseSseBlock). */
function sseBody(events: Array<{ event: string; data: unknown }>): string {
  return events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

function requestEmail(route: Route): string {
  try {
    const body = route.request().postDataJSON() as { email?: unknown } | null;
    if (body && typeof body.email === "string" && body.email) {
      return body.email;
    }
  } catch {
    // У запроса нет JSON-тела.
  }
  return "e2e@example.com";
}

function requestText(route: Route): string | null {
  try {
    const body = route.request().postDataJSON() as { text?: unknown } | null;
    if (body && typeof body.text === "string") {
      return body.text;
    }
  } catch {
    // У запроса нет JSON-тела.
  }
  return null;
}

/**
 * Состояние «сервера» для auth-моков в рамках одной страницы (теста).
 * Повторяет cookie-сессию реального backend: refresh работает только после
 * логина, logout её сбрасывает.
 */
interface AuthMockState {
  loggedIn: boolean;
  email: string;
}

async function handleAuthRoute(
  route: Route,
  state: AuthMockState,
): Promise<void> {
  const request = route.request();
  const method = request.method();
  const path = new URL(request.url()).pathname;

  if (method === "POST" && path === "/auth/register") {
    state.email = requestEmail(route);
    await fulfillJson(route, 201, authUser(state.email));
    return;
  }
  if (method === "POST" && path === "/auth/login") {
    state.loggedIn = true;
    state.email = requestEmail(route);
    await fulfillJson(route, 200, {
      ...authTokens(),
      user: authUser(state.email),
    });
    return;
  }
  if (method === "POST" && path === "/auth/refresh") {
    // Cookie есть только после логина — иначе bootstrap получает 401.
    if (!state.loggedIn) {
      await fulfillJson(route, 401, {
        detail: "unauthorized",
        code: "unauthorized",
      });
      return;
    }
    await fulfillJson(route, 200, {
      ...authTokens(),
      user: authUser(state.email),
    });
    return;
  }
  if (method === "POST" && path === "/auth/logout") {
    state.loggedIn = false;
    // 204 без тела: cookie и refresh-сессия «сброшены».
    await route.fulfill({ status: 204 });
    return;
  }
  if (method === "GET" && path === "/auth/me") {
    await fulfillJson(route, 200, authUser(state.email));
    return;
  }
  await fulfillJson(route, 404, {
    detail: `e2e mock: ${method} ${path} не реализован`,
    code: "mock_not_implemented",
  });
}

async function handleV1Route(route: Route): Promise<void> {
  const request = route.request();
  const method = request.method();
  const path = new URL(request.url()).pathname;

  if (method === "GET" && path === "/v1/users/me/usage") {
    await fulfillJson(route, 200, usagePayload());
    return;
  }

  if (method === "POST" && path === "/v1/analyze") {
    await fulfillJson(route, 200, mockAnalysis(MOCK_ANALYSIS_ID));
    return;
  }

  if (method === "GET" && path === "/v1/games") {
    await fulfillJson(route, 200, gameList());
    return;
  }

  const gameMatch = path.match(/^\/v1\/games\/([^/]+)$/);
  if (method === "GET" && gameMatch) {
    await fulfillJson(route, 200, mockAnalysis(decodeURIComponent(gameMatch[1])));
    return;
  }

  if (method === "POST" && path === "/v1/commentary/stream") {
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      },
      body: sseBody([
        { event: "meta", data: { model: "mock-llm", level: "club" } },
        ...MOCK_COMMENTARY_DELTAS.map((text) => ({
          event: "delta",
          data: { text },
        })),
        {
          event: "done",
          data: {
            commentary_id: MOCK_COMMENTARY_ID,
            tokens_used: 123,
            model: "mock-llm",
            validated: true,
          },
        },
      ]),
    });
    return;
  }

  const commentaryMatch = path.match(/^\/v1\/commentaries\/(\d+)$/);
  if (method === "PATCH" && commentaryMatch) {
    const text = requestText(route) ?? "";
    const edited: EditedCommentary = {
      commentary_id: Number(commentaryMatch[1]),
      game_id: Number(MOCK_ANALYSIS_ID),
      level: "club",
      text,
      human_edited: true,
      validated: true,
      model: "mock-llm",
      edit_id: MOCK_EDIT_ID,
      created_at: "2026-10-28T12:10:00Z",
    };
    await fulfillJson(route, 200, edited);
    return;
  }

  await fulfillJson(route, 404, {
    detail: `e2e mock: ${method} ${path} не реализован`,
    code: "mock_not_implemented",
  });
}

/**
 * Подключить моки API к странице. В real-backend-режиме — no-op.
 * Вызывается в каждом spec через beforeEach.
 *
 * Паттерны привязаны к origin, чтобы не перехватывать модули Vite dev server.
 */
export async function mockApi(page: Page): Promise<void> {
  if (REAL_BACKEND) {
    return;
  }
  // Состояние auth-моков живёт в рамках страницы (новый context на тест).
  const authState: AuthMockState = {
    loggedIn: false,
    email: "e2e@example.com",
  };
  await page.route(/^https?:\/\/[^/]+\/auth\//, (route) =>
    handleAuthRoute(route, authState),
  );
  await page.route(/^https?:\/\/[^/]+\/v1\//, handleV1Route);
}

/**
 * Регистрация + вход через реальный UI (без сидирования сессии: access-токен
 * живёт только в памяти, refresh — в HttpOnly cookie): так сценарий
 * одинаково работает и на моках, и на реальном backend.
 * Возвращает email пользователя.
 */
export async function registerAndLogin(
  page: Page,
  email: string = uniqueEmail(),
  password: string = PASSWORD,
): Promise<string> {
  await page.goto("/login");

  await page.getByRole("tab", { name: "Регистрация" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Создать аккаунт" }).click();

  // После регистрации LoginPage сразу логинит пользователя и уводит на "/".
  await expect(page).toHaveURL(/\/$/, {
    timeout: REAL_BACKEND ? 120_000 : 15_000,
  });
  await expect(page.getByText(`Вы вошли как ${email}`)).toBeVisible({
    timeout: REAL_BACKEND ? 30_000 : 10_000,
  });
  return email;
}

/** Экспорт `test` из fixtures, чтобы спеки не дублировали импорт. */
export const test = base;

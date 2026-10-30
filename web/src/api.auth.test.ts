import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthTokens, AuthUser, LoginResponse, RefreshResponse } from "./types";

const EMAIL = "player@example.com";
const PASSWORD = "password123";

const TOKENS: AuthTokens = {
  access_token: "access-old",
  token_type: "bearer",
  expires_in: 900,
};

const REFRESHED: RefreshResponse = {
  access_token: "access-new",
  token_type: "bearer",
  expires_in: 900,
  user: {
    id: 7,
    email: EMAIL,
    level: "club",
    role: "user",
    is_active: true,
    is_verified: true,
    created_at: "2026-10-29T10:00:00Z",
  },
};

const USER: AuthUser = REFRESHED.user;

const LOGIN_RESPONSE: LoginResponse = {
  ...TOKENS,
  user: USER,
};

const USAGE = {
  tier: "free",
  used_tokens: 10,
  limit_tokens: 1000,
  remaining_tokens: 990,
  reset_at: 0,
  enabled: true,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function callAt(
  mock: ReturnType<typeof vi.fn>,
  index: number,
): { url: string; init: RequestInit } {
  const call = mock.mock.calls[index] as
    | [string, RequestInit | undefined]
    | undefined;
  if (!call) {
    throw new Error(`Нет вызова fetch #${index}`);
  }
  return { url: String(call[0]), init: call[1] ?? {} };
}

function authHeader(init: RequestInit): string | null {
  return new Headers(init.headers).get("Authorization");
}

let storage: {
  getItem: ReturnType<typeof vi.fn>;
  setItem: ReturnType<typeof vi.fn>;
  removeItem: ReturnType<typeof vi.fn>;
  clear: ReturnType<typeof vi.fn>;
  key: ReturnType<typeof vi.fn>;
  readonly length: number;
};
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.resetModules();
  storage = {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
    key: vi.fn(() => null),
    get length() {
      return 0;
    },
  };
  vi.stubGlobal("localStorage", storage as unknown as Storage);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function authorize(): Promise<void> {
  const { useAuthStore } = await import("./stores/authStore");
  useAuthStore.getState().setSession(TOKENS, USER);
}

describe("api: Bearer из памяти и cookie-запросы", () => {
  it("добавляет Bearer и credentials:'include', когда пользователь авторизован", async () => {
    await authorize();
    const { getUsage } = await import("./api");
    fetchMock.mockResolvedValueOnce(jsonResponse(USAGE));

    await getUsage();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/v1/users/me/usage");
    expect(authHeader(init)).toBe("Bearer access-old");
    expect(init.credentials).toBe("include");
  });

  it("не добавляет Authorization анонимно, но шлёт cookie", async () => {
    const { getUsage } = await import("./api");
    fetchMock.mockResolvedValueOnce(jsonResponse(USAGE));

    await getUsage();

    const { init } = callAt(fetchMock, 0);
    expect(authHeader(init)).toBeNull();
    expect(init.credentials).toBe("include");
  });

  it("на 401 обновляет access-токен по cookie и повторяет исходный запрос", async () => {
    await authorize();
    const { listGames } = await import("./api");
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
      )
      .mockResolvedValueOnce(jsonResponse(REFRESHED))
      .mockResolvedValueOnce(jsonResponse([]));

    const games = await listGames();

    expect(games).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    const original = callAt(fetchMock, 0);
    expect(original.url).toBe("/v1/games?limit=20");
    expect(authHeader(original.init)).toBe("Bearer access-old");
    expect(original.init.credentials).toBe("include");

    // Refresh — POST без тела: refresh-токен уходит только HttpOnly cookie.
    const refresh = callAt(fetchMock, 1);
    expect(refresh.url).toBe("/auth/refresh");
    expect(refresh.init.method).toBe("POST");
    expect(refresh.init.credentials).toBe("include");
    expect(refresh.init.body).toBeUndefined();
    expect(new Headers(refresh.init.headers).get("Content-Type")).toBeNull();

    const retry = callAt(fetchMock, 2);
    expect(retry.url).toBe("/v1/games?limit=20");
    expect(authHeader(retry.init)).toBe("Bearer access-new");
    expect(retry.init.credentials).toBe("include");

    const { useAuthStore } = await import("./stores/authStore");
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-new");
    expect(state.user).toEqual(USER);
    // Access-токен остаётся только в памяти.
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("делает один refresh на параллельные 401 (single-flight)", async () => {
    await authorize();
    const { listGames } = await import("./api");
    let refreshCalls = 0;
    let protectedCalls = 0;
    fetchMock.mockImplementation(
      (input: unknown, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url === "/auth/refresh") {
          refreshCalls += 1;
          expect(init?.credentials).toBe("include");
          expect(init?.body).toBeUndefined();
          return Promise.resolve(jsonResponse(REFRESHED));
        }
        protectedCalls += 1;
        expect(init?.credentials).toBe("include");
        if (authHeader(init ?? {}) === "Bearer access-old") {
          return Promise.resolve(
            jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
          );
        }
        return Promise.resolve(jsonResponse([]));
      },
    );

    const [first, second] = await Promise.all([listGames(), listGames()]);

    expect(first).toEqual([]);
    expect(second).toEqual([]);
    expect(refreshCalls).toBe(1);
    expect(protectedCalls).toBe(4);
  });

  it("при неудачном refresh очищает сессию и бросает ApiError 401", async () => {
    await authorize();
    const { ApiError, listGames } = await import("./api");
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
      )
      .mockResolvedValueOnce(
        jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
      );

    let caught: unknown = null;
    try {
      await listGames();
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({
      status: 401,
      code: "unauthorized",
      message: "unauthorized",
    });

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("loginUser шлёт /auth/login с credentials и ставит сессию из ответа", async () => {
    const { loginUser } = await import("./api");
    fetchMock.mockResolvedValueOnce(jsonResponse(LOGIN_RESPONSE));

    const result = await loginUser(EMAIL, PASSWORD);

    expect(result.access_token).toBe("access-old");
    expect(result.user.email).toBe(EMAIL);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/login");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({
      email: EMAIL,
      password: PASSWORD,
    });

    const { useAuthStore } = await import("./stores/authStore");
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-old");
    expect(state.user).toEqual(USER);
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("loginUser не пытается обновить токен при 401", async () => {
    const { loginUser } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
    );

    await expect(loginUser(EMAIL, "wrong-password")).rejects.toMatchObject({
      status: 401,
      code: "unauthorized",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("registerUser шлёт /auth/register и не создаёт сессию", async () => {
    const { registerUser } = await import("./api");
    fetchMock.mockResolvedValueOnce(jsonResponse(USER, 201));

    const profile = await registerUser(EMAIL, PASSWORD);

    expect(profile.email).toBe(EMAIL);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/register");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({
      email: EMAIL,
      password: PASSWORD,
    });

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("logoutUser шлёт POST /auth/logout с cookie и очищает сессию", async () => {
    await authorize();
    const { logoutUser } = await import("./api");
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await logoutUser();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("logoutUser очищает сессию даже при сетевой ошибке", async () => {
    await authorize();
    const { logoutUser } = await import("./api");
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(logoutUser()).resolves.toBeUndefined();

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("logoutUser очищает сессию при 5xx и не пробрасывает ошибку", async () => {
    await authorize();
    const { logoutUser } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "internal error" }, 500),
    );

    await expect(logoutUser()).resolves.toBeUndefined();

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("streamRequest шлёт Bearer и credentials:'include'", async () => {
    await authorize();
    const { streamCommentary } = await import("./api");
    const sse =
      'event: done\ndata: {"commentary_id":1,"tokens_used":5,"validated":true}\n\n';
    fetchMock.mockResolvedValueOnce(
      new Response(sse, {
        status: 200,
        headers: { "Content-Type": "text/event-stream; charset=utf-8" },
      }),
    );

    const deltas: string[] = [];
    const done = await streamCommentary("42", "club", {
      onDelta: (text) => deltas.push(text),
    });

    expect(deltas).toEqual([]);
    expect(done.commentary_id).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/v1/commentary/stream");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(authHeader(init)).toBe("Bearer access-old");
  });

  it("streamCommentary передаёт нормализованный язык i18n (T-106)", async () => {
    await authorize();
    const { streamCommentary } = await import("./api");
    const sse =
      'event: done\ndata: {"commentary_id":1,"tokens_used":5,"validated":true}\n\n';
    fetchMock.mockResolvedValueOnce(
      new Response(sse, {
        status: 200,
        headers: { "Content-Type": "text/event-stream; charset=utf-8" },
      }),
    );

    await streamCommentary(
      "42",
      "club",
      { onDelta: () => undefined },
      "en-US",
    );

    const { init } = callAt(fetchMock, 0);
    expect(JSON.parse(String(init.body))).toMatchObject({
      analysis_id: 42,
      level: "club",
      language: "en",
    });
  });

  it("generateCommentary передаёт язык в JSON-запрос (T-106)", async () => {
    await authorize();
    const { generateCommentary } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        commentary_id: 7,
        text: "English commentary.",
        tokens_used: 10,
        validated: true,
        model: "mock",
        attempts: 1,
      }),
    );

    await generateCommentary("42", "gm", "ru");

    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/v1/commentary");
    expect(JSON.parse(String(init.body))).toEqual({
      analysis_id: 42,
      level: "gm",
      language: "ru",
    });
  });

  it("streamChat передаёт нормализованный язык i18n (KI-058)", async () => {
    await authorize();
    const { streamChat } = await import("./api");
    const sse = 'event: done\ndata: {"tokens_used":5,"validated":true}\n\n';
    fetchMock.mockResolvedValueOnce(
      new Response(sse, {
        status: 200,
        headers: { "Content-Type": "text/event-stream; charset=utf-8" },
      }),
    );

    await streamChat(
      "42",
      "club",
      "Who played white?",
      { onDelta: () => undefined },
      [],
      "en-US",
    );

    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/v1/chat/stream");
    expect(JSON.parse(String(init.body))).toMatchObject({
      analysis_id: 42,
      level: "club",
      message: "Who played white?",
      language: "en",
    });
  });
});

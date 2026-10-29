import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthTokens, AuthUser, LoginResponse } from "./types";

const EMAIL = "player@example.com";
const PASSWORD = "password123";

const TOKENS: AuthTokens = {
  access_token: "access-old",
  refresh_token: "refresh-old",
  token_type: "bearer",
  expires_in: 900,
};

const REFRESHED_TOKENS: AuthTokens = {
  access_token: "access-new",
  refresh_token: "refresh-new",
  token_type: "bearer",
  expires_in: 900,
};

const USER: AuthUser = {
  id: 7,
  email: EMAIL,
  level: "club",
  role: "user",
  is_active: true,
  created_at: "2026-10-29T10:00:00Z",
};

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

function createStorageMock(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear() {
      data.clear();
    },
    getItem(key: string) {
      return data.has(key) ? (data.get(key) ?? null) : null;
    },
    key(index: number) {
      return Array.from(data.keys())[index] ?? null;
    },
    removeItem(key: string) {
      data.delete(key);
    },
    setItem(key: string, value: string) {
      data.set(key, value);
    },
  };
}

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

let storage: Storage;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.resetModules();
  storage = createStorageMock();
  vi.stubGlobal("localStorage", storage);
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

describe("api JWT", () => {
  it("добавляет Bearer, когда пользователь авторизован", async () => {
    await authorize();
    const { getUsage } = await import("./api");
    fetchMock.mockResolvedValueOnce(jsonResponse(USAGE));

    await getUsage();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/v1/users/me/usage");
    expect(authHeader(init)).toBe("Bearer access-old");
  });

  it("не добавляет Authorization анонимно", async () => {
    const { getUsage } = await import("./api");
    fetchMock.mockResolvedValueOnce(jsonResponse(USAGE));

    await getUsage();

    const { init } = callAt(fetchMock, 0);
    expect(authHeader(init)).toBeNull();
  });

  it("на 401 обновляет токен и повторяет исходный запрос", async () => {
    await authorize();
    const { listGames } = await import("./api");
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
      )
      .mockResolvedValueOnce(jsonResponse(REFRESHED_TOKENS))
      .mockResolvedValueOnce(jsonResponse([]));

    const games = await listGames();

    expect(games).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(3);

    const original = callAt(fetchMock, 0);
    expect(original.url).toBe("/v1/games?limit=20");
    expect(authHeader(original.init)).toBe("Bearer access-old");

    const refresh = callAt(fetchMock, 1);
    expect(refresh.url).toBe("/auth/refresh");
    expect(refresh.init.method).toBe("POST");
    expect(JSON.parse(String(refresh.init.body))).toEqual({
      refresh_token: "refresh-old",
    });

    const retry = callAt(fetchMock, 2);
    expect(retry.url).toBe("/v1/games?limit=20");
    expect(authHeader(retry.init)).toBe("Bearer access-new");

    const { useAuthStore, AUTH_STORAGE_KEY } = await import(
      "./stores/authStore"
    );
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-new");
    expect(state.refreshToken).toBe("refresh-new");
    expect(state.user).toEqual(USER);
    expect(
      JSON.parse(storage.getItem(AUTH_STORAGE_KEY) ?? "null").accessToken,
    ).toBe("access-new");
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
          return Promise.resolve(jsonResponse(REFRESHED_TOKENS));
        }
        protectedCalls += 1;
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

    const { useAuthStore, AUTH_STORAGE_KEY } = await import(
      "./stores/authStore"
    );
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().refreshToken).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("loginUser шлёт /auth/login и сохраняет сессию", async () => {
    const { loginUser } = await import("./api");
    fetchMock.mockResolvedValueOnce(jsonResponse(LOGIN_RESPONSE));

    const result = await loginUser(EMAIL, PASSWORD);

    expect(result.access_token).toBe("access-old");
    expect(result.user.email).toBe(EMAIL);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      email: EMAIL,
      password: PASSWORD,
    });

    const { useAuthStore } = await import("./stores/authStore");
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-old");
    expect(state.refreshToken).toBe("refresh-old");
    expect(state.user).toEqual(USER);
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
    expect(JSON.parse(String(init.body))).toEqual({
      email: EMAIL,
      password: PASSWORD,
    });

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("logoutUser очищает сессию без запросов к API", async () => {
    await authorize();
    const { logoutUser } = await import("./api");

    logoutUser();

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthTokens, AuthUser, RefreshResponse } from "../types";

const TOKENS: AuthTokens = {
  access_token: "access-token-1",
  token_type: "bearer",
  expires_in: 900,
};

const REFRESHED: RefreshResponse = {
  access_token: "access-token-2",
  token_type: "bearer",
  expires_in: 900,
  user: {
    id: 7,
    email: "player@example.com",
    level: "club",
    role: "user",
    is_active: true,
    created_at: "2026-10-29T10:00:00Z",
  },
};

const USER: AuthUser = {
  id: 7,
  email: "player@example.com",
  level: "club",
  role: "user",
  is_active: true,
  created_at: "2026-10-29T10:00:00Z",
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

describe("authStore: сессия только в памяти", () => {
  it("стартует без сессии, в состоянии bootstrap и без записи в localStorage", async () => {
    const { useAuthStore, selectIsAuthenticated } = await import(
      "./authStore"
    );

    const state = useAuthStore.getState();

    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
    expect(selectIsAuthenticated(state)).toBe(false);
    // Refresh-cookie ещё не проверена — AuthGate обязан показать loading.
    expect(state.initializing).toBe(true);
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("setSession хранит access-токен и профиль только в памяти", async () => {
    const { useAuthStore, selectIsAuthenticated } = await import(
      "./authStore"
    );

    useAuthStore.getState().setSession(TOKENS, USER);

    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-token-1");
    expect(state.user).toEqual(USER);
    expect(state.isAuthenticated).toBe(true);
    expect(selectIsAuthenticated(state)).toBe(true);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.getItem).not.toHaveBeenCalled();
  });

  it("setSession без user обновляет только access-токен", async () => {
    const { useAuthStore } = await import("./authStore");

    useAuthStore.getState().setSession(TOKENS, USER);
    useAuthStore.getState().setSession({
      ...TOKENS,
      access_token: "access-token-2",
    });

    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-token-2");
    expect(state.user).toEqual(USER);
  });

  it("setUser обновляет профиль без localStorage", async () => {
    const { useAuthStore } = await import("./authStore");

    useAuthStore.getState().setSession(TOKENS, USER);
    useAuthStore.getState().setUser({ ...USER, email: "new@example.com" });

    expect(useAuthStore.getState().user?.email).toBe("new@example.com");
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("clearSession сбрасывает access-токен, профиль и флаг авторизации", async () => {
    const { useAuthStore } = await import("./authStore");

    useAuthStore.getState().setSession(TOKENS, USER);
    useAuthStore.getState().clearSession();

    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });
});

describe("authStore.bootstrapAuth: один POST /auth/refresh по cookie", () => {
  it("200 → восстанавливает сессию из ответа refresh", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(REFRESHED));
    const { useAuthStore, bootstrapAuth } = await import("./authStore");

    await bootstrapAuth();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/refresh");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    // Тело не отправляется: refresh-токен браузер берёт из HttpOnly cookie.
    expect(init.body).toBeUndefined();

    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-token-2");
    expect(state.user).toEqual(USER);
    expect(state.isAuthenticated).toBe(true);
    expect(state.initializing).toBe(false);
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("401 → аноним, без исключения, initializing снят", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
    );
    const { useAuthStore, bootstrapAuth } = await import("./authStore");

    await expect(bootstrapAuth()).resolves.toBeUndefined();

    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
    expect(state.initializing).toBe(false);
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("сетевая ошибка → аноним, без исключения", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { useAuthStore, bootstrapAuth } = await import("./authStore");

    await expect(bootstrapAuth()).resolves.toBeUndefined();

    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.initializing).toBe(false);
  });

  it("single-flight: повторные вызовы не дублируют refresh", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(REFRESHED));
    const { bootstrapAuth } = await import("./authStore");

    await Promise.all([bootstrapAuth(), bootstrapAuth(), bootstrapAuth()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("после неудачи следующий bootstrap пробует refresh снова", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
      )
      .mockResolvedValueOnce(jsonResponse(REFRESHED));
    const { useAuthStore, bootstrapAuth } = await import("./authStore");

    await bootstrapAuth();
    expect(useAuthStore.getState().accessToken).toBeNull();

    await bootstrapAuth();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useAuthStore.getState().accessToken).toBe("access-token-2");
  });
});

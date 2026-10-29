import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthTokens, AuthUser } from "../types";

const ACCESS_TOKEN = "access-token-1";
const REFRESH_TOKEN = "refresh-token-1";

const TOKENS: AuthTokens = {
  access_token: ACCESS_TOKEN,
  refresh_token: REFRESH_TOKEN,
  token_type: "bearer",
  expires_in: 900,
};

const USER: AuthUser = {
  id: 7,
  email: "player@example.com",
  level: "club",
  role: "user",
  is_active: true,
  created_at: "2026-10-29T10:00:00Z",
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

let storage: Storage;

beforeEach(() => {
  vi.resetModules();
  storage = createStorageMock();
  vi.stubGlobal("localStorage", storage);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("authStore", () => {
  it("стартует без сессии и без записи в localStorage", async () => {
    const { useAuthStore, selectIsAuthenticated } = await import(
      "./authStore"
    );

    const state = useAuthStore.getState();

    expect(state.accessToken).toBeNull();
    expect(state.refreshToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
    expect(selectIsAuthenticated(state)).toBe(false);
    expect(storage.getItem("chess.auth")).toBeNull();
  });

  it("setSession сохраняет токены, профиль и localStorage", async () => {
    const { useAuthStore, selectIsAuthenticated, AUTH_STORAGE_KEY } =
      await import("./authStore");

    useAuthStore.getState().setSession(TOKENS, USER);

    const state = useAuthStore.getState();
    expect(state.accessToken).toBe(ACCESS_TOKEN);
    expect(state.refreshToken).toBe(REFRESH_TOKEN);
    expect(state.user).toEqual(USER);
    expect(state.isAuthenticated).toBe(true);
    expect(selectIsAuthenticated(state)).toBe(true);
    expect(
      JSON.parse(storage.getItem(AUTH_STORAGE_KEY) ?? "null"),
    ).toEqual({
      accessToken: ACCESS_TOKEN,
      refreshToken: REFRESH_TOKEN,
      user: USER,
    });
  });

  it("setSession без user обновляет только токены", async () => {
    const { useAuthStore } = await import("./authStore");

    useAuthStore.getState().setSession(TOKENS, USER);
    useAuthStore.getState().setSession({
      ...TOKENS,
      access_token: "access-token-2",
      refresh_token: "refresh-token-2",
    });

    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("access-token-2");
    expect(state.refreshToken).toBe("refresh-token-2");
    expect(state.user).toEqual(USER);
  });

  it("hydrateAuth восстанавливает сессию после перезагрузки модуля", async () => {
    const first = await import("./authStore");
    first.useAuthStore.getState().setSession(TOKENS, USER);

    vi.resetModules();
    const second = await import("./authStore");

    // Свежий store пуст до гидратации.
    expect(second.useAuthStore.getState().accessToken).toBeNull();

    const restored = second.hydrateAuth();

    expect(restored?.accessToken).toBe(ACCESS_TOKEN);
    expect(second.useAuthStore.getState().accessToken).toBe(ACCESS_TOKEN);
    expect(second.useAuthStore.getState().refreshToken).toBe(REFRESH_TOKEN);
    expect(second.useAuthStore.getState().user).toEqual(USER);
    expect(second.useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it("setUser обновляет профиль и persisted-запись", async () => {
    const { useAuthStore, AUTH_STORAGE_KEY } = await import("./authStore");

    useAuthStore.getState().setSession(TOKENS, USER);
    useAuthStore.getState().setUser({ ...USER, email: "new@example.com" });

    expect(useAuthStore.getState().user?.email).toBe("new@example.com");
    const persisted = JSON.parse(storage.getItem(AUTH_STORAGE_KEY) ?? "{}");
    expect(persisted.user.email).toBe("new@example.com");
  });

  it("clearSession сбрасывает состояние и удаляет localStorage", async () => {
    const { useAuthStore, AUTH_STORAGE_KEY } = await import("./authStore");

    useAuthStore.getState().setSession(TOKENS, USER);
    useAuthStore.getState().clearSession();

    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.refreshToken).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("безопасно работает без localStorage", async () => {
    vi.unstubAllGlobals();
    const { useAuthStore, hydrateAuth } = await import("./authStore");

    expect(hydrateAuth()).toBeNull();
    expect(() =>
      useAuthStore.getState().setSession(TOKENS, USER),
    ).not.toThrow();
    expect(useAuthStore.getState().accessToken).toBe(ACCESS_TOKEN);
    expect(() => useAuthStore.getState().clearSession()).not.toThrow();
  });
});

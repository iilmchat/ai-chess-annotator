import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthTokens, AuthUser } from "./types";

const EMAIL = "player@example.com";
const TOKEN = "reset/verify+token ==";

const TOKENS: AuthTokens = {
  access_token: "access-old",
  token_type: "bearer",
  expires_in: 900,
};

const USER: AuthUser = {
  id: 7,
  email: EMAIL,
  level: "club",
  role: "user",
  is_active: true,
  is_verified: true,
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

describe("api: verify email (KI-055)", () => {
  it("шлёт GET /auth/verify-email с URL-encoded токеном без Bearer", async () => {
    const { verifyEmail } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ email: EMAIL, is_verified: true }),
    );

    const result = await verifyEmail(TOKEN);

    expect(result).toEqual({ email: EMAIL, is_verified: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe(`/auth/verify-email?token=${encodeURIComponent(TOKEN)}`);
    expect(url).not.toContain(TOKEN);
    expect(init.method).toBeUndefined();
    expect(init.credentials).toBe("include");
    expect(authHeader(init)).toBeNull();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("401 не запускает refresh: токен письма не access-токен", async () => {
    const { ApiError, verifyEmail } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "invalid token", code: "unauthorized" }, 401),
    );

    await expect(verifyEmail(TOKEN)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const { url } = callAt(fetchMock, 0);
    expect(url).not.toBe("/auth/refresh");
  });
});

describe("api: forgot/reset password (KI-055)", () => {
  it("forgotPassword шлёт POST с email и не запускает refresh", async () => {
    const { forgotPassword } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { detail: "Если email зарегистрирован, письмо отправлено" },
        202,
      ),
    );

    const result = await forgotPassword(EMAIL);

    expect(result.detail).toContain("письмо отправлено");
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/forgot-password");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({ email: EMAIL });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("resetPassword шлёт token и new_password в теле", async () => {
    const { resetPassword } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Пароль обновлён" }, 200),
    );

    await resetPassword(TOKEN, "NewPassword456!");

    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/reset-password");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      token: TOKEN,
      new_password: "NewPassword456!",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("resetPassword с 401 не делает refresh и бросает ApiError", async () => {
    const { ApiError, resetPassword } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "used", code: "unauthorized" }, 401),
    );

    await expect(
      resetPassword(TOKEN, "NewPassword456!"),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(callAt(fetchMock, 0).url).toBe("/auth/reset-password");
  });
});

describe("api: logout-all (KI-055)", () => {
  it("шлёт POST /auth/logout-all с Bearer, принимает 204 и чистит сессию", async () => {
    await authorize();
    const { logoutAllUser } = await import("./api");
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await logoutAllUser();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/auth/logout-all");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(authHeader(init)).toBe("Bearer access-old");

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("очищает сессию даже при сетевой ошибке", async () => {
    await authorize();
    const { logoutAllUser } = await import("./api");
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(logoutAllUser()).resolves.toBeUndefined();

    const { useAuthStore } = await import("./stores/authStore");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

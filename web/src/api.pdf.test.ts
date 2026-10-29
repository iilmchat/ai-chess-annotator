import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthTokens, AuthUser, RefreshResponse } from "./types";

const TOKENS: AuthTokens = {
  access_token: "access-old",
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

const REFRESHED: RefreshResponse = {
  access_token: "access-new",
  token_type: "bearer",
  expires_in: 900,
  user: USER,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pdfResponse(): Response {
  return new Response(new Blob(["%PDF-1.4 test"], { type: "application/pdf" }), {
    status: 200,
    headers: { "Content-Type": "application/pdf" },
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

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
    key: vi.fn(() => null),
    length: 0,
  } as unknown as Storage);
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

describe("api: PDF комментария", () => {
  it("скачивает blob с Bearer и credentials:'include'", async () => {
    await authorize();
    const { fetchCommentaryPdf } = await import("./api");
    fetchMock.mockResolvedValueOnce(pdfResponse());

    const blob = await fetchCommentaryPdf(7);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const { url, init } = callAt(fetchMock, 0);
    expect(url).toBe("/v1/commentaries/7/export/pdf");
    expect(authHeader(init)).toBe("Bearer access-old");
    expect(init.credentials).toBe("include");
    expect(blob.type).toBe("application/pdf");
  });

  it("анонимный запрос идёт без Authorization", async () => {
    const { fetchCommentaryPdf } = await import("./api");
    fetchMock.mockResolvedValueOnce(pdfResponse());

    await fetchCommentaryPdf(3);

    const { init } = callAt(fetchMock, 0);
    expect(authHeader(init)).toBeNull();
    expect(init.credentials).toBe("include");
  });

  it("ошибку сервера превращает в ApiError", async () => {
    await authorize();
    const { fetchCommentaryPdf } = await import("./api");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "не найдено", code: "not_found" }, 404),
    );

    await expect(fetchCommentaryPdf(1)).rejects.toMatchObject({
      status: 404,
      code: "not_found",
      message: "не найдено",
    });
  });

  it("при 401 обновляет access-токен и повторяет запрос", async () => {
    await authorize();
    const { fetchCommentaryPdf } = await import("./api");
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ detail: "unauthorized", code: "unauthorized" }, 401),
      )
      .mockResolvedValueOnce(jsonResponse(REFRESHED))
      .mockResolvedValueOnce(pdfResponse());

    const blob = await fetchCommentaryPdf(9);

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(callAt(fetchMock, 1).url).toBe("/auth/refresh");
    const retry = callAt(fetchMock, 2);
    expect(retry.url).toBe("/v1/commentaries/9/export/pdf");
    expect(authHeader(retry.init)).toBe("Bearer access-new");
    expect(blob.type).toBe("application/pdf");
  });
});

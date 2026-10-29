import { getAuthState } from "./stores/authStore";
import type {
  AnalyzeResponse,
  AuthTokens,
  AuthUser,
  ChatHistoryItem,
  ChatResponse,
  CommentaryResponse,
  EditedCommentary,
  GameListItem,
  Level,
  LoginResponse,
  RegisterResponse,
  UsageResponse,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/** Эти эндпоинты сами выдают/обновляют токены — refresh для них запрещён. */
const NO_REFRESH_PATHS = new Set([
  "/auth/login",
  "/auth/register",
  "/auth/refresh",
]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function send(
  path: string,
  init: RequestInit | undefined,
  accessToken: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  try {
    return await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    throw new ApiError("Сервер недоступен. Проверьте, запущен ли API.", 0, null);
  }
}

async function throwForResponse(response: Response): Promise<never> {
  let detail = `HTTP ${response.status}`;
  let code: string | null = null;
  try {
    const body = (await response.json()) as { detail?: string; code?: string };
    detail = body.detail ?? detail;
    code = body.code ?? null;
  } catch {
    // Тело не JSON — оставляем HTTP-статус.
  }
  throw new ApiError(detail, response.status, code);
}

/**
 * Один общий refresh на все запросы: параллельные 401 ждут один и тот же
 * промис, после успеха сессия обновляется в authStore.
 */
let refreshPromise: Promise<AuthTokens> | null = null;

async function performRefresh(): Promise<AuthTokens> {
  const refreshToken = getAuthState().refreshToken;
  if (!refreshToken) {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  if (!response.ok) {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  let payload: AuthTokens & { user?: AuthUser };
  try {
    payload = (await response.json()) as AuthTokens & { user?: AuthUser };
  } catch {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  if (!payload.access_token || !payload.refresh_token) {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  getAuthState().setSession(payload, payload.user);
  return payload;
}

/** Обновляет пару токенов (single-flight); возвращает новую пару. */
export function refreshSession(): Promise<AuthTokens> {
  if (!refreshPromise) {
    refreshPromise = performRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAuthState().accessToken;
  let response = await send(path, init, token);

  if (response.status === 401 && !NO_REFRESH_PATHS.has(path)) {
    const latest = getAuthState().accessToken;
    // Пока запрос летел, другой 401 уже обновил токен — просто повторяем.
    const retryToken =
      latest && latest !== token
        ? latest
        : (await refreshSession()).access_token;
    response = await send(path, init, retryToken);
  }

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as T;
}

/** `POST /auth/register` — создаёт пользователя, токены не выдаёт. */
export function registerUser(
  email: string,
  password: string,
): Promise<RegisterResponse> {
  return request<RegisterResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

/** `POST /auth/login` — сохраняет токены и профиль в authStore. */
export async function loginUser(
  email: string,
  password: string,
): Promise<LoginResponse> {
  const session = await request<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  getAuthState().setSession(session, session.user);
  return session;
}

/** `GET /auth/me` — профиль текущего пользователя. */
export function getMe(): Promise<AuthUser> {
  return request<AuthUser>("/auth/me");
}

/** Локальный выход: backend logout-эндпоинта нет. */
export function logoutUser(): void {
  getAuthState().clearSession();
}

export function analyzeGame(pgn: string, level: Level): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>("/v1/analyze", {
    method: "POST",
    body: JSON.stringify({ pgn, level }),
  });
}

export function getGame(analysisId: string): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>(`/v1/games/${analysisId}`);
}

export function listGames(limit = 20): Promise<GameListItem[]> {
  return request<GameListItem[]>(`/v1/games?limit=${limit}`);
}

export function getUsage(): Promise<UsageResponse> {
  return request<UsageResponse>("/v1/users/me/usage");
}

export function generateCommentary(
  analysisId: string,
  level: Level,
): Promise<CommentaryResponse> {
  return request<CommentaryResponse>("/v1/commentary", {
    method: "POST",
    body: JSON.stringify({ analysis_id: Number(analysisId), level }),
  });
}

export function updateCommentary(
  commentaryId: number,
  text: string,
): Promise<EditedCommentary> {
  return request<EditedCommentary>(`/v1/commentaries/${commentaryId}`, {
    method: "PATCH",
    body: JSON.stringify({ text }),
  });
}

export function askChat(
  analysisId: string,
  level: Level,
  message: string,
  history: ChatHistoryItem[] = [],
): Promise<ChatResponse> {
  return request<ChatResponse>("/v1/chat", {
    method: "POST",
    body: JSON.stringify({
      analysis_id: Number(analysisId),
      level,
      message,
      history,
    }),
  });
}

export interface StreamMeta {
  model: string;
  level: string;
}

export interface StreamDone {
  commentary_id?: number;
  tokens_used: number;
  model?: string;
  validated: boolean;
}

export interface StreamHandlers {
  onDelta: (text: string) => void;
  onMeta?: (meta: StreamMeta) => void;
  signal?: AbortSignal;
}

export async function streamCommentary(
  analysisId: string,
  level: Level,
  handlers: StreamHandlers,
): Promise<StreamDone> {
  return streamRequest(
    "/v1/commentary/stream",
    { analysis_id: Number(analysisId), level },
    handlers,
  );
}

export async function streamChat(
  analysisId: string,
  level: Level,
  message: string,
  handlers: StreamHandlers,
  history: ChatHistoryItem[] = [],
): Promise<StreamDone> {
  return streamRequest(
    "/v1/chat/stream",
    {
      analysis_id: Number(analysisId),
      level,
      message,
      history,
    },
    handlers,
  );
}

async function streamRequest(
  path: string,
  body: Record<string, unknown>,
  handlers: StreamHandlers,
): Promise<StreamDone> {
  const token = getAuthState().accessToken;

  async function open(accessToken: string | null): Promise<Response> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    };
    if (accessToken) {
      headers.Authorization = `Bearer ${accessToken}`;
    }
    try {
      return await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: handlers.signal,
      });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        throw cause;
      }
      throw new ApiError("Сервер недоступен. Проверьте, запущен ли API.", 0, null);
    }
  }

  let response = await open(token);
  if (response.status === 401) {
    const latest = getAuthState().accessToken;
    const retryToken =
      latest && latest !== token
        ? latest
        : (await refreshSession()).access_token;
    response = await open(retryToken);
  }

  if (!response.ok) {
    return throwForResponse(response);
  }
  if (!response.body) {
    throw new ApiError("Сервер не вернул поток", response.status, null);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: StreamDone | null = null;

  while (true) {
    const { value, done: finished } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: true });
    }
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseSseBlock(block);
      if (parsed) {
        if (parsed.event === "error") {
          const error = parsed.payload as { code?: string; detail?: string };
          throw new ApiError(
            error.detail ?? "Ошибка стриминга",
            502,
            error.code ?? null,
          );
        }
        if (parsed.event === "meta") {
          handlers.onMeta?.(parsed.payload as unknown as StreamMeta);
        } else if (parsed.event === "delta") {
          const text = (parsed.payload as { text?: string }).text ?? "";
          if (text) {
            handlers.onDelta(text);
          }
        } else if (parsed.event === "done") {
          done = parsed.payload as unknown as StreamDone;
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (finished) {
      break;
    }
  }

  if (!done) {
    throw new ApiError("Поток завершился без события done", 502, null);
  }
  return done;
}

export function parseSseBlock(
  block: string,
): { event: string | null; payload: Record<string, unknown> } | null {
  const lines = block.split("\n");
  const dataLines: string[] = [];
  let event: string | null = null;
  for (const line of lines) {
    if (line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!dataLines.length) {
    return null;
  }
  try {
    const payload = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
    return { event, payload };
  } catch {
    return null;
  }
}

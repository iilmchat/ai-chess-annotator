import { getAuthState } from "./stores/authStore";
import type {
  AnalyzeResponse,
  AuthUser,
  BillingSessionResponse,
  ChatHistoryItem,
  ChatResponse,
  CommentaryResponse,
  EditedCommentary,
  EmailVerificationResponse,
  GameListItem,
  Level,
  LoginResponse,
  MessageResponse,
  RefreshResponse,
  RegisterResponse,
  UsageResponse,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/**
 * Эти эндпоинты сами выдают/обновляют токены либо отвечают 401 не из-за
 * истёкшего access-токена — refresh для них запрещён.
 */
const NO_REFRESH_PATHS = new Set([
  "/auth/login",
  "/auth/register",
  "/auth/refresh",
  // KI-055: токен из письма проверяется как есть; 401 означает «токен
  // недействителен», а не «access истёк» — refresh только маскировал бы это.
  "/auth/verify-email",
  "/auth/forgot-password",
  "/auth/reset-password",
  // T-082: 401 с code invalid_credentials — это неверный текущий пароль,
  // а не протухший access-токен; повтор после refresh заново отправлял бы
  // неверный пароль (и мог бы дважды считать попытку).
  "/auth/change-password",
]);

/** Путь без query-строки — `verify-email` приходит с `?token=…`. */
function isNoRefreshPath(path: string): boolean {
  return NO_REFRESH_PATHS.has(path.split("?")[0]);
}

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
    return await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      // Refresh-токен приходит/уходит только HttpOnly cookie (KI-037).
      credentials: "include",
    });
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
 * промис, после успеха сессия обновляется в authStore. Refresh-токен браузер
 * отправляет сам в HttpOnly cookie (`credentials: "include"`), тела нет.
 */
let refreshPromise: Promise<RefreshResponse> | null = null;

async function performRefresh(): Promise<RefreshResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
  } catch {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  if (!response.ok) {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  let payload: RefreshResponse;
  try {
    payload = (await response.json()) as RefreshResponse;
  } catch {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  if (!payload.access_token) {
    getAuthState().clearSession();
    throw new ApiError("unauthorized", 401, "unauthorized");
  }

  // Cookie ротирована backend'ом — JS её не читает и не хранит.
  getAuthState().setSession(payload, payload.user);
  return payload;
}

/** Обновляет access-токен (single-flight); возвращает новую сессию. */
export function refreshSession(): Promise<RefreshResponse> {
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

  if (response.status === 401 && !isNoRefreshPath(path)) {
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

  // Некоторые эндпоинты (например, `POST /auth/change-password`) отвечают
  // 200 с пустым телом — `response.json()` на нём упал бы с SyntaxError.
  const text = await response.text();
  if (!text) {
    return undefined as unknown as T;
  }
  return JSON.parse(text) as T;
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

/** `POST /auth/login` — сохраняет access-токен и профиль в authStore. */
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

/**
 * `PATCH /auth/me` — сохранить отображаемое имя и предпочитаемый уровень
 * (T-082). Поля, которых нет в `payload`, backend не меняет; `display_name`
 * обрезается по краям на стороне backend (пустое имя отклоняется 422).
 */
export function updateProfile(payload: {
  display_name?: string;
  level?: Level;
}): Promise<AuthUser> {
  return request<AuthUser>("/auth/me", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/**
 * `POST /auth/change-password` — сменить пароль под текущей сессией (T-082).
 *
 * Ответ 200 может быть с пустым телом — тело ответа не используется.
 */
export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  return request<void>("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}

/**
 * `POST /auth/logout` — backend очищает HttpOnly cookie и отзывает refresh.
 * Сетевую ошибку не пробрасываем: локальный выход важнее, сессию чистим всегда.
 */
export async function logoutUser(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
  } catch {
    // Backend недоступен — всё равно выходим локально.
  } finally {
    getAuthState().clearSession();
  }
}

/**
 * `POST /auth/logout-all` — глобально отозвать refresh-токены (KI-041).
 *
 * Ответ 204 без тела, поэтому не используем общий `request()` (он парсит
 * JSON). Сетевую/серверную ошибку не пробрасываем: пользователь нажал
 * «выйти», локальная сессия должна исчезнуть в любом случае. Backend мог
 * не отозвать токены (fail-open), но refresh-cookie всё равно очищается.
 */
export async function logoutAllUser(): Promise<void> {
  try {
    const token = getAuthState().accessToken;
    let response = await send("/auth/logout-all", { method: "POST" }, token);
    if (response.status === 401) {
      const latest = getAuthState().accessToken;
      const retryToken =
        latest && latest !== token
          ? latest
          : (await refreshSession()).access_token;
      response = await send(
        "/auth/logout-all",
        { method: "POST" },
        retryToken,
      );
    }
    if (!response.ok) {
      await throwForResponse(response);
    }
  } catch {
    // Локальный выход важнее ответа сервера (как у logoutUser).
  } finally {
    getAuthState().clearSession();
  }
}

/** `GET /auth/verify-email?token=…` — подтвердить email по ссылке из письма. */
export function verifyEmail(token: string): Promise<EmailVerificationResponse> {
  return request<EmailVerificationResponse>(
    `/auth/verify-email?token=${encodeURIComponent(token)}`,
  );
}

/** `POST /auth/forgot-password` — запросить письмо сброса (ответ нейтрален). */
export function forgotPassword(email: string): Promise<MessageResponse> {
  return request<MessageResponse>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/** `POST /auth/reset-password` — сменить пароль по токену из письма. */
export function resetPassword(
  token: string,
  newPassword: string,
): Promise<MessageResponse> {
  return request<MessageResponse>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
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

/**
 * `POST /v1/billing/create-checkout-session` — URL Stripe Checkout (T-083).
 *
 * Тело пустое: success/cancel URL и цену backend знает сам, клиент не может
 * подменить redirect на произвольный внешний адрес.
 */
export function createCheckoutSession(): Promise<BillingSessionResponse> {
  return request<BillingSessionResponse>("/v1/billing/create-checkout-session", {
    method: "POST",
  });
}

/** `POST /v1/billing/create-portal-session` — URL Stripe Customer Portal. */
export function createPortalSession(): Promise<BillingSessionResponse> {
  return request<BillingSessionResponse>("/v1/billing/create-portal-session", {
    method: "POST",
  });
}

/** Нормализовать язык i18n (`en-US` → `en`) для LLM-промптов. */
function requestLanguage(language?: string): "ru" | "en" | undefined {
  if (!language) {
    return undefined;
  }
  return language.toLowerCase().startsWith("en") ? "en" : "ru";
}

export function generateCommentary(
  analysisId: string,
  level: Level,
  language?: string,
): Promise<CommentaryResponse> {
  return request<CommentaryResponse>("/v1/commentary", {
    method: "POST",
    body: JSON.stringify({
      analysis_id: Number(analysisId),
      level,
      language: requestLanguage(language),
    }),
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

/**
 * `GET /v1/commentaries/{id}/export/pdf` — PDF-файл комментария (T-107).
 *
 * Запрос защищён JWT, поэтому берём blob вручную (общий `request()`
 * парсит JSON). При 401 — тот же single-flight refresh, что и у JSON-
 * запросов; ошибки сервера превращаются в `ApiError`. `language`
 * нормализуется (`en-US` → `en`) и уходит query-параметром (KI-058);
 * без языка сервер решает по `Accept-Language`.
 */
export async function fetchCommentaryPdf(
  commentaryId: number,
  language?: string,
): Promise<Blob> {
  const lang = requestLanguage(language);
  const query = lang ? `?language=${lang}` : "";
  const path = `/v1/commentaries/${commentaryId}/export/pdf${query}`;
  const token = getAuthState().accessToken;
  let response = await send(path, undefined, token);

  if (response.status === 401) {
    const latest = getAuthState().accessToken;
    const retryToken =
      latest && latest !== token
        ? latest
        : (await refreshSession()).access_token;
    response = await send(path, undefined, retryToken);
  }

  if (!response.ok) {
    return throwForResponse(response);
  }
  return response.blob();
}

/** Скачать PDF комментария в браузере (создаёт временную ссылку). */
export async function downloadCommentaryPdf(
  commentaryId: number,
  language?: string,
): Promise<void> {
  const blob = await fetchCommentaryPdf(commentaryId, language);
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = `commentary-${commentaryId}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Тело запроса чата с контекстом выбранного полухода (T-072).
 *
 * `selected_ply` уходит только для реального хода (`ply > 0`): 0 — это
 * стартовая позиция, `null`/`undefined` — контекста нет. Backend трактует
 * отсутствие поля как «без контекста», поэтому для 0 поле не отправляем.
 */
function chatBody(
  body: Record<string, unknown>,
  selectedPly?: number | null,
): Record<string, unknown> {
  if (typeof selectedPly !== "number" || selectedPly <= 0) {
    return body;
  }
  return { ...body, selected_ply: selectedPly };
}

export function askChat(
  analysisId: string,
  level: Level,
  message: string,
  history: ChatHistoryItem[] = [],
  language?: string,
  selectedPly?: number | null,
): Promise<ChatResponse> {
  return request<ChatResponse>("/v1/chat", {
    method: "POST",
    body: JSON.stringify(
      chatBody(
        {
          analysis_id: Number(analysisId),
          level,
          message,
          history,
          language: requestLanguage(language),
        },
        selectedPly,
      ),
    ),
  });
}

export interface StreamMeta {
  model: string;
  level: string;
  /** T-106: язык сгенерированного комментария. */
  language?: "ru" | "en";
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
  language?: string,
): Promise<StreamDone> {
  return streamRequest(
    "/v1/commentary/stream",
    {
      analysis_id: Number(analysisId),
      level,
      language: requestLanguage(language),
    },
    handlers,
  );
}

export async function streamChat(
  analysisId: string,
  level: Level,
  message: string,
  handlers: StreamHandlers,
  history: ChatHistoryItem[] = [],
  language?: string,
  selectedPly?: number | null,
): Promise<StreamDone> {
  return streamRequest(
    "/v1/chat/stream",
    chatBody(
      {
        analysis_id: Number(analysisId),
        level,
        message,
        history,
        language: requestLanguage(language),
      },
      selectedPly,
    ),
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
        // Cookie нужна только для /auth/*; для SSE-запросов она безвредна.
        credentials: "include",
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

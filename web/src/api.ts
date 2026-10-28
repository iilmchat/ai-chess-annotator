import type {
  AnalyzeResponse,
  ChatResponse,
  CommentaryResponse,
  EditedCommentary,
  Level,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError("Сервер недоступен. Проверьте, запущен ли API.", 0, null);
  }

  if (!response.ok) {
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

  return (await response.json()) as T;
}

export function analyzeGame(pgn: string, level: Level): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>("/v1/analyze", {
    method: "POST",
    body: JSON.stringify({ pgn, level }),
  });
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
): Promise<ChatResponse> {
  return request<ChatResponse>("/v1/chat", {
    method: "POST",
    body: JSON.stringify({
      analysis_id: Number(analysisId),
      level,
      message,
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
): Promise<StreamDone> {
  return streamRequest(
    "/v1/chat/stream",
    { analysis_id: Number(analysisId), level, message },
    handlers,
  );
}

async function streamRequest(
  path: string,
  body: Record<string, unknown>,
  handlers: StreamHandlers,
): Promise<StreamDone> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal: handlers.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    throw new ApiError("Сервер недоступен. Проверьте, запущен ли API.", 0, null);
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    let code: string | null = null;
    try {
      const payload = (await response.json()) as { detail?: string; code?: string };
      detail = payload.detail ?? detail;
      code = payload.code ?? null;
    } catch {
      // Не JSON.
    }
    throw new ApiError(detail, response.status, code);
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

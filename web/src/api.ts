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

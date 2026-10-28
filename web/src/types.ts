export type Level = "beginner" | "club" | "gm";

export interface Motif {
  type: string;
  description: string;
  importance: number;
}

export interface RecommendedMove {
  san: string;
  uci: string;
  eval_cp: number | null;
  mate: number | null;
}

export interface AnalyzedMove {
  ply: number;
  san: string;
  uci: string;
  fen: string;
  eval_cp: number | null;
  mate: number | null;
  best_move_san: string;
  delta_cp: number;
  classification: string;
  motifs: Motif[];
  recommended_move: RecommendedMove | null;
}

export interface AnalyzeResponse {
  analysis_id: string;
  status: "completed";
  meta: {
    white: string | null;
    black: string | null;
    eco: string | null;
  };
  initial_fen: string;
  analysis_level?: Level;
  moves_analyzed: number;
  time_taken_ms: number;
  summary: {
    blunders: number;
    mistakes: number;
    inaccuracies: number;
  };
  moves: AnalyzedMove[];
  commentaries?: GameCommentary[];
  created_at?: string;
}

export interface GameCommentary {
  id: number;
  level: Level;
  text: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  validated: boolean;
  human_edited: boolean;
  created_at: string;
}

export interface GameListItem {
  id: number;
  white: string | null;
  black: string | null;
  event: string | null;
  level: Level;
  created_at: string;
}

export interface UsageResponse {
  tier: "free" | "pro";
  used_tokens: number;
  limit_tokens: number;
  remaining_tokens: number;
  reset_at: number;
  enabled: boolean;
}

export interface ChatHistoryItem {
  role: "user" | "assistant";
  content: string;
}

export interface CommentaryResponse {
  commentary_id: number;
  text: string;
  tokens_used: number;
  validated: boolean;
  model: string;
  attempts: number;
}

export interface EditedCommentary {
  commentary_id: number;
  game_id: number;
  level: Level;
  text: string;
  human_edited: boolean;
  validated: boolean;
  model: string;
  edit_id: number;
  created_at: string;
}

export interface ChatResponse {
  reply: string;
  tokens_used: number;
}

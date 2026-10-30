import { Chess, type Move } from "chess.js";

import type { AnalyzedMove } from "../types";

export interface PgnPosition {
  ply: number;
  san: string;
  uci: string;
  fen: string;
}

export interface PgnPositions {
  initialFen: string;
  positions: PgnPosition[];
}

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

export const DEMO_PGN = `[Event "Demo"]
[Site "ChessAnnotator"]
[Date "2026.10.28"]
[White "Svidler"]
[Black "Shirov"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5
7. Bb3 d6 8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 1-0`;

/**
 * Восстановить FEN каждой позиции из PGN на клиенте.
 *
 * `chess.js` проигрывает партию и отдаёт `history({verbose:true})`, где у
 * каждого хода есть `before`/`after`. Это позволяет не доверять серверу
 * в отрисовке доски и поддержать FEN-старт через `[SetUp]/[FEN]`.
 */
export function positionsFromPgn(pgn: string): PgnPositions {
  const chess = new Chess();
  try {
    chess.loadPgn(pgn);
  } catch (error) {
    throw new Error(
      error instanceof Error ? `PGN: ${error.message}` : "PGN не разобран",
    );
  }
  const history = chess.history({ verbose: true });
  const initialFen = history[0]?.before ?? START_FEN;
  return {
    initialFen,
    positions: history.map((move, index) => ({
      ply: index + 1,
      san: move.san,
      uci: `${move.from}${move.to}${move.promotion ?? ""}`,
      fen: move.after,
    })),
  };
}

/** Порядок тегов в шапке: сначала семь обязательных, затем остальные. */
const HEADER_ORDER = [
  "Event",
  "Site",
  "Date",
  "Round",
  "White",
  "Black",
  "Result",
  "Annotator",
];

const VALID_RESULTS = new Set(["1-0", "0-1", "1/2-1/2", "*"]);

export interface AnnotatedPgnOptions {
  /** Исходная партия: из неё берутся заголовки и последовательность ходов. */
  pgn: string;
  /** Разбор ходов: оценки/мат/классификация попадают в комментарии. */
  moves: AnalyzedMove[];
  /** Текст комментария партии — добавляется после результата. */
  commentary?: string | null;
}

/**
 * Собрать аннотированный PGN: заголовки исходной партии + комментарий
 * `{оценка, классификация}` к каждому разобранному ходу + (опционально) текст
 * комментария после результата.
 *
 * Комментарии — данные, а не UI-текст: `good`/`blunder` и `+0.35` не
 * переводятся, поэтому файл нейтрален к RU/EN (как PGN/SAN в PDF-экспорте).
 * Фигурные скобки и переводы строк в тексте санитизируются: PGN-комментарий
 * не должен ломаться от пользовательских/LLM-данных.
 *
 * Заголовки берутся из PGN и сохраняются; `Annotator` добавляется, только
 * если его не было. `Result` всегда согласован с результатом в movetext.
 */
export function buildAnnotatedPgn({
  pgn,
  moves,
  commentary,
}: AnnotatedPgnOptions): string {
  if (!pgn.trim()) {
    throw new Error("PGN: пустая партия");
  }
  const chess = new Chess();
  try {
    chess.loadPgn(pgn);
  } catch (error) {
    throw new Error(
      error instanceof Error ? `PGN: ${error.message}` : "PGN не разобран",
    );
  }
  const history = chess.history({ verbose: true });
  if (!history.length) {
    throw new Error("PGN: в партии нет ходов");
  }

  const headers: Record<string, string> = { ...chess.getHeaders() };
  if (!headers.Annotator) {
    headers.Annotator = "ChessAnnotator";
  }
  const result = normaliseResult(headers.Result);
  headers.Result = result;

  // Ply считается от 1: сервер нумерует ходы так же (см. AnalyzeResponse).
  const analysed = new Map<number, AnalyzedMove>();
  for (const move of moves) {
    if (!analysed.has(move.ply)) {
      analysed.set(move.ply, move);
    }
  }

  const movetext = formatMovetext(history, analysed, result).join(" ");
  const commentaryText = commentary ? sanitizePgnText(commentary) : "";
  const trailer = commentaryText ? ` {${commentaryText}}` : "";

  const headerLines = orderedHeaderKeys(headers).map(
    (key) => `[${key} "${sanitizeHeaderValue(headers[key])}"]`,
  );
  return `${headerLines.join("\n")}\n\n${movetext}${trailer}\n`;
}

/**
 * Собрать PGN из списка SAN-ходов, когда исходного PGN под рукой нет.
 *
 * Запасной путь для `AnalysisWorkspace`: страница анализа получает только
 * `analysis.moves` (SAN) и `initialFen`, а не сырой PGN (`AnalysisPage` — вне
 * зоны правки этой итерации), поэтому кнопка «Скачать PGN» восстанавливает
 * партию по SAN, а `buildAnnotatedPgn` уже добавляет комментарии.
 */
export function buildPgnFromSan(
  sans: string[],
  initialFen: string = START_FEN,
): string {
  const fen = initialFen.trim() || START_FEN;
  const fromStandardStart = fen === START_FEN;
  let chess: Chess;
  try {
    chess = fromStandardStart ? new Chess() : new Chess(fen);
  } catch {
    throw new Error("PGN: не разобран FEN начальной позиции");
  }
  for (const san of sans) {
    chess.move(san);
  }

  chess.setHeader("Event", "ChessAnnotator");
  chess.setHeader("Site", "ChessAnnotator");
  chess.setHeader("Date", pgnDate());
  chess.setHeader("Round", "?");
  chess.setHeader("White", "?");
  chess.setHeader("Black", "?");
  chess.setHeader("Result", "*");
  if (!fromStandardStart) {
    chess.setHeader("SetUp", "1");
    chess.setHeader("FEN", fen);
  }

  const headers = chess.getHeaders();
  const history = chess.history({ verbose: true });
  const headerLines = orderedHeaderKeys(headers).map(
    (key) => `[${key} "${sanitizeHeaderValue(headers[key])}"]`,
  );
  const movetext = formatMovetext(history, new Map(), "*").join(" ");
  return `${headerLines.join("\n")}\n\n${movetext}\n`;
}

/** Имя файла для скачивания: `chessannotator-<analysis_id|game>.pgn`. */
export function buildPgnFilename(analysisId?: string | number | null): string {
  const raw =
    analysisId === undefined || analysisId === null ? "" : String(analysisId);
  const safe = raw
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^[.-]+/, "")
    .replace(/[.-]+$/, "");
  return `chessannotator-${safe || "game"}.pgn`;
}

/**
 * Скачать текст в браузере (Blob + временная ссылка), как PDF в `api.ts`:
 * `URL.createObjectURL` → `<a download>` → `click()` → `revokeObjectURL`.
 */
export function downloadTextFile(
  filename: string,
  text: string,
  mime: string = "application/x-chess-pgn",
): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Movetext с нумерацией и комментариями; результат — последним токеном. */
function formatMovetext(
  history: Move[],
  analysed: Map<number, AnalyzedMove>,
  result: string,
): string[] {
  const tokens: string[] = [];
  history.forEach((move, index) => {
    const fenParts = move.before.split(/\s+/);
    const parsedNumber = Number.parseInt(fenParts[5] ?? "", 10);
    const moveNumber =
      Number.isFinite(parsedNumber) && parsedNumber > 0
        ? parsedNumber
        : Math.floor(index / 2) + 1;
    // Партия из FEN-старта может начинаться с хода чёрных: тогда "20...".
    if (move.color === "w") {
      tokens.push(`${moveNumber}.`);
    } else if (index === 0) {
      tokens.push(`${moveNumber}...`);
    }
    const moveAnalysis = analysed.get(index + 1);
    const comment = moveAnalysis ? moveComment(moveAnalysis) : "";
    tokens.push(comment ? `${move.san} {${comment}}` : move.san);
  });
  tokens.push(result);
  return tokens;
}

/** `{+0.35, good}` — оценка (или мат) и классификация, если они есть. */
function moveComment(move: AnalyzedMove): string {
  const parts: string[] = [];
  const score = scoreLabel(move.eval_cp, move.mate);
  if (score) {
    parts.push(score);
  }
  const classification = sanitizePgnText(move.classification ?? "");
  if (classification) {
    parts.push(classification);
  }
  return parts.join(", ");
}

function scoreLabel(evalCp: number | null, mate: number | null): string | null {
  if (mate !== null && mate !== undefined) {
    return mate > 0 ? `+M${mate}` : `-M${Math.abs(mate)}`;
  }
  if (evalCp === null || evalCp === undefined) {
    return null;
  }
  const value = evalCp / 100;
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

/** Убрать из текста PGN-комментария `{`, `}` и переводы строк. */
function sanitizePgnText(text: string): string {
  return text
    .replace(/[{}]/g, (brace) => (brace === "{" ? "(" : ")"))
    .replace(/\s+/g, " ")
    .trim();
}

function sanitizeHeaderValue(value: string): string {
  return value.replace(/"/g, "'").replace(/\s+/g, " ").trim();
}

function normaliseResult(value: string | undefined): string {
  return value && VALID_RESULTS.has(value) ? value : "*";
}

function orderedHeaderKeys(headers: Record<string, string>): string[] {
  const preferred = HEADER_ORDER.filter((key) => key in headers);
  const rest = Object.keys(headers).filter((key) => !HEADER_ORDER.includes(key));
  return [...preferred, ...rest];
}

function pgnDate(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())}`;
}

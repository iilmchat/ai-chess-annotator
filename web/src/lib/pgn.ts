import { Chess } from "chess.js";

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

import { useMemo } from "react";

import { parseFen, uciSquares } from "../lib/board";

interface BoardProps {
  fen: string;
  lastMove?: string | null;
}

export function Board({ fen, lastMove }: BoardProps) {
  const highlighted = lastMove ? uciSquares(lastMove) : null;
  const board = useMemo(() => {
    try {
      return { rows: parseFen(fen), error: null };
    } catch (error) {
      return {
        rows: [],
        error: error instanceof Error ? error.message : "Некорректный FEN",
      };
    }
  }, [fen]);

  if (board.error) {
    return <div className="board-error">Не удалось показать доску: {board.error}</div>;
  }

  return (
    <div className="board" role="grid" aria-label="Шахматная доска">
      {board.rows.flat().map((cell) => {
        const isFrom = highlighted?.from === cell.square;
        const isTo = highlighted?.to === cell.square;
        return (
          <div
            key={cell.square}
            role="gridcell"
            aria-label={cell.piece ? `${cell.square}: ${cell.piece}` : cell.square}
            className={[
              "board__square",
              cell.light ? "board__square--light" : "board__square--dark",
              isFrom || isTo ? "board__square--last" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {cell.piece ? (
              <span
                className={`board__piece ${
                  "♟♞♝♜♛♚".includes(cell.piece)
                    ? "board__piece--black"
                    : "board__piece--white"
                }`}
              >
                {cell.piece}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

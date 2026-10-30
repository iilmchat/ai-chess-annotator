import { Chessboard, type Arrow } from "react-chessboard";

import type { BoardArrow } from "../lib/arrows";
import { uciSquares } from "../lib/board";

interface BoardProps {
  fen: string;
  lastMove?: string | null;
  /** Разрешено ли перетаскивание фигур (режим интерактивного разбора). */
  interactive?: boolean;
  /**
   * Колбэк перетаскивания фигуры. Возвращает `true`, если ход принят:
   * иначе react-chessboard откатывает фигуру на исходную клетку.
   */
  onPieceDrop?: (from: string, to: string) => boolean;
  /**
   * Стрелки поверх доски (T-073): рекомендация и ходы из комментария.
   * Пустой массив/`undefined` — стрелок нет.
   */
  arrows?: BoardArrow[];
}

export function Board({
  fen,
  lastMove,
  interactive = false,
  onPieceDrop,
  arrows = [],
}: BoardProps) {
  const move = lastMove ? uciSquares(lastMove) : null;
  const squareStyles = move
    ? {
        [move.from]: { boxShadow: "inset 0 0 0 4px rgba(250, 204, 21, 0.85)" },
        [move.to]: { boxShadow: "inset 0 0 0 4px rgba(250, 204, 21, 0.85)" },
      }
    : undefined;

  // react-chessboard рисует стрелку по паре startSquare/endSquare, а цвет
  // подставляет в SVG как есть: CSS-имена green/red работают.
  const boardArrows: Arrow[] = arrows.map((arrow) => ({
    startSquare: arrow.from,
    endSquare: arrow.to,
    color: arrow.color,
  }));

  return (
    <div className="board-shell">
      <Chessboard
        options={{
          position: fen,
          arrows: boardArrows,
          boardOrientation: "white",
          // В обычном режиме (без разбора) доска остаётся «просмотровой».
          allowDragging: interactive === true,
          onPieceDrop: interactive
            ? ({ sourceSquare, targetSquare }) =>
                targetSquare
                  ? onPieceDrop?.(sourceSquare, targetSquare) ?? false
                  : false
            : undefined,
          showNotation: true,
          animationDurationInMs: 160,
          squareStyles,
          boardStyle: {
            width: "100%",
            maxWidth: "620px",
            borderRadius: "16px",
            overflow: "hidden",
            boxShadow: "0 24px 70px rgba(0, 0, 0, 0.4)",
          },
          darkSquareStyle: { backgroundColor: "#7c5b3a" },
          lightSquareStyle: { backgroundColor: "#e8d8b8" },
        }}
      />
    </div>
  );
}

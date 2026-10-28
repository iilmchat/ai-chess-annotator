import { Chessboard } from "react-chessboard";

import { uciSquares } from "../lib/board";

interface BoardProps {
  fen: string;
  lastMove?: string | null;
}

export function Board({ fen, lastMove }: BoardProps) {
  const move = lastMove ? uciSquares(lastMove) : null;
  const squareStyles = move
    ? {
        [move.from]: { boxShadow: "inset 0 0 0 4px rgba(250, 204, 21, 0.85)" },
        [move.to]: { boxShadow: "inset 0 0 0 4px rgba(250, 204, 21, 0.85)" },
      }
    : undefined;

  return (
    <div className="board-shell">
      <Chessboard
        options={{
          position: fen,
          boardOrientation: "white",
          allowDragging: false,
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

const PIECES: Record<string, string> = {
  p: "♟",
  n: "♞",
  b: "♝",
  r: "♜",
  q: "♛",
  k: "♚",
  P: "♙",
  N: "♘",
  B: "♗",
  R: "♖",
  Q: "♕",
  K: "♔",
};

export interface BoardCell {
  square: string;
  piece: string | null;
  light: boolean;
}

const FILES = "abcdefgh";

export function parseFen(fen: string): BoardCell[][] {
  const placement = fen.trim().split(/\s+/)[0];
  const ranks = placement.split("/");
  if (ranks.length !== 8) {
    throw new Error(`FEN: ожидалось 8 горизонталей, получено ${ranks.length}`);
  }

  return ranks.map((rank, rowIndex) => {
    const cells: BoardCell[] = [];
    for (const char of rank) {
      if (/^\d$/.test(char)) {
        const empty = Number(char);
        for (let index = 0; index < empty; index += 1) {
          cells.push(makeCell(rowIndex, cells.length));
        }
      } else {
        const piece = PIECES[char];
        if (!piece) {
          throw new Error(`FEN: неизвестная фигура «${char}»`);
        }
        cells.push({ ...makeCell(rowIndex, cells.length), piece });
      }
    }
    if (cells.length !== 8) {
      throw new Error(`FEN: в горизонтали ${8 - rowIndex} не 8 клеток`);
    }
    return cells;
  });
}

export function uciSquares(uci: string): { from: string; to: string } | null {
  const match = /^([a-h][1-8])([a-h][1-8])[qrbn]?$/i.exec(uci);
  if (!match) {
    return null;
  }
  return { from: match[1].toLowerCase(), to: match[2].toLowerCase() };
}

function makeCell(rowIndex: number, columnIndex: number): BoardCell {
  return {
    square: `${FILES[columnIndex]}${8 - rowIndex}`,
    piece: null,
    light: (rowIndex + columnIndex) % 2 === 0,
  };
}

import type { Level } from "../types";

export const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

export function formatEval(
  evalCp: number | null,
  mate: number | null,
): string {
  if (mate !== null) {
    return mate > 0 ? `+M${mate}` : `-M${Math.abs(mate)}`;
  }
  if (evalCp === null) {
    return "—";
  }
  const value = evalCp / 100;
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

const CLASS_LABELS: Record<string, string> = {
  blunder: "Зевок",
  mistake: "Ошибка",
  inaccuracy: "Неточность",
  good: "Хорошо",
  best: "Лучший",
  brilliant: "Блестящий",
};

export function classificationLabel(classification: string): string {
  return CLASS_LABELS[classification] ?? classification;
}

export function levelLabel(level: Level): string {
  return { beginner: "Новичок", club: "Клуб", gm: "Гроссмейстер" }[level];
}

export function movePrefix(ply: number): string {
  const number = Math.ceil(ply / 2);
  return ply % 2 === 1 ? `${number}.` : `${number}...`;
}

export function formatTokens(tokens: number): string {
  return `${tokens.toLocaleString("ru-RU")} токенов`;
}

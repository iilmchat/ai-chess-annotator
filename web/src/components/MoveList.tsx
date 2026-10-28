import { useTranslation } from "react-i18next";

import { classificationLabel, formatEval, movePrefix } from "../lib/format";
import type { AnalyzedMove } from "../types";

interface MoveListProps {
  moves: AnalyzedMove[];
  selectedPly: number;
  onSelect: (ply: number) => void;
}

export function MoveList({ moves, selectedPly, onSelect }: MoveListProps) {
  const { t } = useTranslation();

  return (
    <section className="moves" aria-label={t("moves.title")}>
      <div className="moves__header">
        <h2>{t("moves.title")}</h2>
        <span className="muted">{t("moves.count", { count: moves.length })}</span>
      </div>
      <div className="moves__grid">
        <button
          type="button"
          className={`move move--start ${selectedPly === 0 ? "move--active" : ""}`}
          onClick={() => onSelect(0)}
        >
          {t("moves.start")}
        </button>
        {moves.map((move) => {
          const label = t(`class.${move.classification}`, {
            defaultValue: classificationLabel(move.classification),
          });
          return (
            <button
              key={move.ply}
              type="button"
              className={`move move--${move.classification} ${
                selectedPly === move.ply ? "move--active" : ""
              }`}
              onClick={() => onSelect(move.ply)}
              title={`${move.san}: ${label}, ${move.delta_cp} cp`}
            >
              <span className="move__prefix">{movePrefix(move.ply)}</span>
              <span className="move__san">{move.san}</span>
              <span className="move__eval">
                {formatEval(move.eval_cp, move.mate)}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

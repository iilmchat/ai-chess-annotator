import { classificationLabel, formatEval, movePrefix } from "../lib/format";
import type { AnalyzedMove } from "../types";

interface MoveListProps {
  moves: AnalyzedMove[];
  selectedPly: number;
  onSelect: (ply: number) => void;
}

export function MoveList({ moves, selectedPly, onSelect }: MoveListProps) {
  return (
    <section className="moves" aria-label="Ходы партии">
      <div className="moves__header">
        <h2>Ходы</h2>
        <span className="muted">{moves.length} полуходов</span>
      </div>
      <div className="moves__grid">
        <button
          type="button"
          className={`move move--start ${selectedPly === 0 ? "move--active" : ""}`}
          onClick={() => onSelect(0)}
        >
          Начало
        </button>
        {moves.map((move) => (
          <button
            key={move.ply}
            type="button"
            className={`move move--${move.classification} ${
              selectedPly === move.ply ? "move--active" : ""
            }`}
            onClick={() => onSelect(move.ply)}
            title={`${move.san}: ${classificationLabel(move.classification)}, потеря ${move.delta_cp} cp`}
          >
            <span className="move__prefix">{movePrefix(move.ply)}</span>
            <span className="move__san">{move.san}</span>
            <span className="move__eval">
              {formatEval(move.eval_cp, move.mate)}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

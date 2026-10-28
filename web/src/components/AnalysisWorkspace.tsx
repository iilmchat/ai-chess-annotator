import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { formatEval, movePrefix, START_FEN } from "../lib/format";
import type { PgnPosition } from "../lib/pgn";
import type { AnalyzeResponse, Level } from "../types";
import { Board } from "./Board";
import { ChatPanel } from "./ChatPanel";
import { CommentaryPanel } from "./CommentaryPanel";
import { MoveList } from "./MoveList";
import { Skeleton } from "./Skeleton";

interface AnalysisWorkspaceProps {
  analysis: AnalyzeResponse | null;
  positions: PgnPosition[];
  initialFen: string;
  selectedPly: number;
  level: Level;
  onSelectPly: (ply: number) => void;
  leftPanel?: ReactNode;
  busy?: boolean;
}

export function AnalysisWorkspace({
  analysis,
  positions,
  initialFen,
  selectedPly,
  level,
  onSelectPly,
  leftPanel,
  busy = false,
}: AnalysisWorkspaceProps) {
  const { t } = useTranslation();
  const selectedPosition = useMemo(
    () => positions.find((item) => item.ply === selectedPly) ?? null,
    [positions, selectedPly],
  );
  const selectedMove = useMemo(
    () => analysis?.moves.find((move) => move.ply === selectedPly) ?? null,
    [analysis, selectedPly],
  );
  const fen =
    selectedPosition?.fen ??
    (initialFen || analysis?.initial_fen || START_FEN);

  return (
    <main className="layout">
      <aside className="column column--left">
        {leftPanel}
        {busy && !analysis ? (
          <section className="panel">
            <Skeleton lines={4} />
          </section>
        ) : null}

        {analysis ? (
          <section className="panel summary">
            <div className="panel__header">
              <h2>{t("summary.title")}</h2>
              <span className="muted">
                {t("common.seconds", {
                  value: (analysis.time_taken_ms / 1000).toFixed(1),
                })}
              </span>
            </div>
            <p className="summary__players">
              {analysis.meta.white ?? "—"} — {analysis.meta.black ?? "—"}
              {analysis.meta.eco ? ` · ${analysis.meta.eco}` : ""}
            </p>
            <div className="summary__pills">
              <span className="pill pill--blunder">
                {t("summary.blunders", { count: analysis.summary.blunders })}
              </span>
              <span className="pill pill--mistake">
                {t("summary.mistakes", { count: analysis.summary.mistakes })}
              </span>
              <span className="pill pill--inaccuracy">
                {t("summary.inaccuracies", {
                  count: analysis.summary.inaccuracies,
                })}
              </span>
            </div>
            <p className="muted">
              {t("summary.level", { level: t(`level.${level}`) })} ·{" "}
              {t("summary.moves", { count: analysis.moves_analyzed })}
            </p>
          </section>
        ) : null}

        {analysis ? (
          <MoveList
            moves={analysis.moves}
            selectedPly={selectedPly}
            onSelect={onSelectPly}
          />
        ) : null}
      </aside>

      <section className="column column--center">
        {busy && !analysis ? (
          <div className="skeleton skeleton--board" />
        ) : (
          <div className="board-wrap">
            <Board fen={fen} lastMove={selectedPosition?.uci ?? null} />
          </div>
        )}

        {selectedMove ? (
          <section className="panel move-detail">
            <div className="move-detail__head">
              <div>
                <span className="muted">{movePrefix(selectedMove.ply)}</span>{" "}
                <strong>{selectedMove.san}</strong>{" "}
                <span className={`tag tag--${selectedMove.classification}`}>
                  {t(`class.${selectedMove.classification}`, {
                    defaultValue: selectedMove.classification,
                  })}
                </span>
              </div>
              <span className="eval-big">
                {formatEval(selectedMove.eval_cp, selectedMove.mate)}
              </span>
            </div>
            <div className="move-detail__facts">
              <span>
                {t("board.best")}: {selectedMove.best_move_san}
              </span>
              <span>
                {t("board.loss")}:{" "}
                {t("board.cp", { value: selectedMove.delta_cp })}
              </span>
              {selectedMove.recommended_move ? (
                <span>
                  {t("board.recommendation")}:{" "}
                  {selectedMove.recommended_move.san} (
                  {formatEval(
                    selectedMove.recommended_move.eval_cp,
                    selectedMove.recommended_move.mate,
                  )}
                  )
                </span>
              ) : null}
            </div>
            {selectedMove.motifs.length > 0 ? (
              <ul className="motifs">
                {selectedMove.motifs.map((motif) => (
                  <li key={`${motif.type}-${motif.description}`}>
                    <strong>{motif.type}</strong> — {motif.description}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">{t("board.noMotifs")}</p>
            )}
          </section>
        ) : (
          <section className="panel move-detail">
            <h2>{t("board.startTitle")}</h2>
            <p className="muted">{t("board.startHint")}</p>
          </section>
        )}
      </section>

      <aside className="column column--right">
        <CommentaryPanel
          analysisId={analysis?.analysis_id ?? null}
          level={level}
        />
        <ChatPanel analysisId={analysis?.analysis_id ?? null} level={level} />
      </aside>
    </main>
  );
}

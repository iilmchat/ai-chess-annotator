import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { formatEval, movePrefix, START_FEN } from "../lib/format";
import {
  buildAnnotatedPgn,
  buildPgnFilename,
  buildPgnFromSan,
  downloadTextFile,
  type PgnPosition,
} from "../lib/pgn";
import type { AnalyzeResponse, Level } from "../types";
import { Board } from "./Board";
import { ChatPanel } from "./ChatPanel";
import { CommentaryPanel } from "./CommentaryPanel";
import { EvalBar } from "./EvalBar";
import { EvalGraph } from "./EvalGraph";
import { MoveList } from "./MoveList";
import { NavigationControls } from "./NavigationControls";
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
  /**
   * Сырой PGN партии, если страница им располагает.
   *
   * `AnalysisPage` получает из store только `analysis`/`positions`/`initialFen`
   * и не передаёт PGN (её правка — вне этой итерации), поэтому для кнопки
   * «Скачать PGN» реализован запасной путь `buildPgnFromSan(analysis.moves)`:
   * партия восстанавливается по SAN, а затем `buildAnnotatedPgn` добавляет
   * комментарии. Когда PGN передан явно, заголовки исходной партии (Event,
   * игроки, ECO, Result) сохраняются как есть.
   */
  pgn?: string;
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
  pgn = "",
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

  /**
   * Скачать аннотированный PGN. Синхронно: оценки/классификации уже есть в
   * `analysis.moves`, поэтому состояние «Готовлю PGN…» не требуется.
   */
  function handlePgn(): void {
    if (!analysis) {
      return;
    }
    const sans = analysis.moves.map((move) => move.san);
    const filename = buildPgnFilename(analysis.analysis_id);
    try {
      const source = pgn.trim() ? pgn : buildPgnFromSan(sans, initialFen);
      let text: string;
      try {
        text = buildAnnotatedPgn({ pgn: source, moves: analysis.moves });
      } catch {
        // Переданный PGN не разобрался (или в нём нет ходов) — собираем
        // партию из SAN, заголовки будут минимальными.
        text = buildAnnotatedPgn({
          pgn: buildPgnFromSan(sans, initialFen),
          moves: analysis.moves,
        });
      }
      downloadTextFile(filename, text);
    } catch {
      // Совсем без комментариев, но пользователь всё равно получит PGN.
      downloadTextFile(filename, buildPgnFromSan(sans, initialFen));
    }
  }

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
              <div className="actions actions--inline">
                <span className="muted">
                  {t("common.seconds", {
                    value: (analysis.time_taken_ms / 1000).toFixed(1),
                  })}
                </span>
                <button
                  type="button"
                  className="button"
                  onClick={handlePgn}
                  disabled={busy}
                  data-testid="download-pgn"
                >
                  {t("analysis.downloadPgn")}
                </button>
              </div>
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
          <>
            <div className="board-row">
              <EvalBar
                evalCp={selectedMove?.eval_cp ?? null}
                mate={selectedMove?.mate ?? null}
              />
              <div className="board-wrap">
                <Board fen={fen} lastMove={selectedPosition?.uci ?? null} />
              </div>
            </div>
            <NavigationControls
              selectedPly={selectedPly}
              totalPlies={positions.length}
              onSelectPly={onSelectPly}
            />
          </>
        )}

        {analysis ? (
          <EvalGraph
            moves={analysis.moves}
            selectedPly={selectedPly}
            onSelectPly={onSelectPly}
          />
        ) : null}

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
                    {/* Тип мотива приходит snake_case из API: переводим, а
                        неизвестный тип оставляем как есть (не теряем данные). */}
                    <strong>
                      {t(`motifs.${motif.type}`, { defaultValue: motif.type })}
                    </strong>{" "}
                    — {motif.description}
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

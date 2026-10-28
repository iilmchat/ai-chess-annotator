import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { analyzeGame, ApiError } from "./api";
import { Board } from "./components/Board";
import { ChatPanel } from "./components/ChatPanel";
import { CommentaryPanel } from "./components/CommentaryPanel";
import { MoveList } from "./components/MoveList";
import { formatEval, movePrefix, START_FEN } from "./lib/format";
import { DEMO_PGN, positionsFromPgn } from "./lib/pgn";
import { useAnalysisStore } from "./stores/analysisStore";
import type { Level } from "./types";

export default function App() {
  const { t, i18n } = useTranslation();
  const {
    pgn,
    level,
    analysis,
    positions,
    initialFen,
    selectedPly,
    setPgn,
    setLevel,
    setAnalysis,
    setSelectedPly,
    reset,
  } = useAnalysisStore();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  async function handleAnalyze() {
    if (!pgn.trim()) {
      setError(t("errors.emptyPgn"));
      return;
    }
    let parsed;
    try {
      parsed = positionsFromPgn(pgn);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("common.unknown"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await analyzeGame(pgn, level);
      setAnalysis(result, parsed.positions, parsed.initialFen);
    } catch (cause) {
      setError(errorMessage(cause, t("common.unknown")));
    } finally {
      setBusy(false);
    }
  }

  function changeLanguage(language: string) {
    void i18n.changeLanguage(language);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("chess.lang", language);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>{t("app.title")}</h1>
          <p className="topbar__subtitle">{t("app.subtitle")}</p>
        </div>
        <div className="topbar__right">
          <span className="badge">{t("app.badge")}</span>
          <div className="lang-switch" role="group" aria-label="Language">
            {(["ru", "en"] as const).map((language) => (
              <button
                key={language}
                type="button"
                className={`lang-switch__button ${
                  i18n.language === language ? "lang-switch__button--active" : ""
                }`}
                onClick={() => changeLanguage(language)}
              >
                {language.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </header>

      {error ? (
        <div className="banner banner--error" role="alert">
          {error}
        </div>
      ) : null}

      <main className="layout">
        <aside className="column column--left">
          <section className="panel">
            <div className="panel__header">
              <h2>{t("pgn.title")}</h2>
              <span className="muted">{t("pgn.hint")}</span>
            </div>
            <textarea
              className="pgn-input"
              value={pgn}
              onChange={(event) => setPgn(event.target.value)}
              spellCheck={false}
              placeholder={t("pgn.placeholder")}
              aria-label={t("pgn.placeholder")}
            />
            <label className="field">
              <span>{t("pgn.level")}</span>
              <select
                value={level}
                onChange={(event) => setLevel(event.target.value as Level)}
              >
                <option value="beginner">{t("level.beginner")}</option>
                <option value="club">{t("level.club")}</option>
                <option value="gm">{t("level.gm")}</option>
              </select>
            </label>
            <div className="actions">
              <button
                type="button"
                className="button button--primary button--wide"
                onClick={() => void handleAnalyze()}
                disabled={busy}
                aria-busy={busy}
              >
                {busy ? t("pgn.analyzing") : t("pgn.analyze")}
              </button>
              <button
                type="button"
                className="button"
                onClick={() => {
                  reset();
                  setPgn(DEMO_PGN);
                }}
                disabled={busy}
              >
                {t("pgn.example")}
              </button>
            </div>
            <p className="hint">{t("pgn.help")}</p>
          </section>

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
              onSelect={setSelectedPly}
            />
          ) : null}
        </aside>

        <section className="column column--center">
          <div className="board-wrap">
            <Board fen={fen} lastMove={selectedPosition?.uci ?? null} />
          </div>

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
                  {t("board.loss")}: {t("board.cp", { value: selectedMove.delta_cp })}
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

      <footer className="footer">
        <span>{t("app.footer")}</span>
        <span>{t("app.footerNote")}</span>
      </footer>
    </div>
  );
}

function errorMessage(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) {
    return cause.code ? `${cause.message} (${cause.code})` : cause.message;
  }
  return cause instanceof Error ? cause.message : fallback;
}

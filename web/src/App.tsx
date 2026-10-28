import { useMemo, useState } from "react";

import { analyzeGame, ApiError } from "./api";
import { Board } from "./components/Board";
import { ChatPanel } from "./components/ChatPanel";
import { CommentaryPanel } from "./components/CommentaryPanel";
import { MoveList } from "./components/MoveList";
import {
  classificationLabel,
  formatEval,
  levelLabel,
  movePrefix,
  START_FEN,
} from "./lib/format";
import type { AnalyzeResponse, Level } from "./types";

const DEFAULT_PGN = `[Event "Demo"]
[Site "ChessAnnotator"]
[Date "2026.10.28"]
[White "Svidler"]
[Black "Shirov"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5
7. Bb3 d6 8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 1-0`;

export default function App() {
  const [pgn, setPgn] = useState(DEFAULT_PGN);
  const [level, setLevel] = useState<Level>("club");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [selectedPly, setSelectedPly] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedMove = useMemo(
    () => analysis?.moves.find((move) => move.ply === selectedPly) ?? null,
    [analysis, selectedPly],
  );

  const fen = selectedMove?.fen ?? analysis?.initial_fen ?? START_FEN;

  async function handleAnalyze() {
    if (!pgn.trim()) {
      setError("Вставьте PGN партии.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await analyzeGame(pgn, level);
      setAnalysis(result);
      setSelectedPly(result.moves.length);
    } catch (cause) {
      setAnalysis(null);
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>ChessAnnotator</h1>
          <p className="topbar__subtitle">
            Комментарий к партии в стиле гроссмейстера — для новичка, клуба и
            гроссмейстера.
          </p>
        </div>
        <span className="badge">MVP v0.1.0</span>
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
              <h2>Партия</h2>
              <span className="muted">PGN или FEN-старт</span>
            </div>
            <textarea
              className="pgn-input"
              value={pgn}
              onChange={(event) => setPgn(event.target.value)}
              spellCheck={false}
              aria-label="PGN партии"
            />
            <label className="field">
              <span>Уровень объяснения</span>
              <select
                value={level}
                onChange={(event) => setLevel(event.target.value as Level)}
              >
                <option value="beginner">Новичок</option>
                <option value="club">Клубный игрок</option>
                <option value="gm">Гроссмейстер</option>
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
                {busy ? "Анализирую… до 30 с" : "Разобрать партию"}
              </button>
              <button
                type="button"
                className="button"
                onClick={() => {
                  setPgn(DEFAULT_PGN);
                  setAnalysis(null);
                  setSelectedPly(0);
                  setError(null);
                }}
                disabled={busy}
              >
                Пример
              </button>
            </div>
            <p className="hint">
              Анализ выполняет Stockfish: оценки, лучшие ходы, мотивы и
              рекомендация под выбранный уровень.
            </p>
          </section>

          {analysis ? (
            <section className="panel summary">
              <div className="panel__header">
                <h2>Сводка</h2>
                <span className="muted">
                  {(analysis.time_taken_ms / 1000).toFixed(1)} с
                </span>
              </div>
              <p className="summary__players">
                {analysis.meta.white ?? "Белые"} — {analysis.meta.black ?? "Чёрные"}
                {analysis.meta.eco ? ` · ${analysis.meta.eco}` : ""}
              </p>
              <div className="summary__pills">
                <span className="pill pill--blunder">
                  Зевков: {analysis.summary.blunders}
                </span>
                <span className="pill pill--mistake">
                  Ошибок: {analysis.summary.mistakes}
                </span>
                <span className="pill pill--inaccuracy">
                  Неточностей: {analysis.summary.inaccuracies}
                </span>
              </div>
              <p className="muted">
                Уровень анализа: {levelLabel(level)} · {analysis.moves_analyzed}{" "}
                полуходов
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
            <Board fen={fen} lastMove={selectedMove?.uci ?? null} />
          </div>

          {selectedMove ? (
            <section className="panel move-detail">
              <div className="move-detail__head">
                <div>
                  <span className="muted">{movePrefix(selectedMove.ply)}</span>{" "}
                  <strong>{selectedMove.san}</strong>{" "}
                  <span className={`tag tag--${selectedMove.classification}`}>
                    {classificationLabel(selectedMove.classification)}
                  </span>
                </div>
                <span className="eval-big">
                  {formatEval(selectedMove.eval_cp, selectedMove.mate)}
                </span>
              </div>
              <div className="move-detail__facts">
                <span>Лучший ход: {selectedMove.best_move_san}</span>
                <span>Потеря: {selectedMove.delta_cp} cp</span>
                {selectedMove.recommended_move ? (
                  <span>
                    Рекомендация: {selectedMove.recommended_move.san} (
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
                <p className="muted">Мотивов на этом ходе не найдено.</p>
              )}
            </section>
          ) : (
            <section className="panel move-detail">
              <h2>Начальная позиция</h2>
              <p className="muted">
                Выберите ход в списке, чтобы увидеть оценку, лучший ход и
                мотивы.
              </p>
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
        <span>ChessAnnotator · MVP v0.1.0</span>
        <span>
          Все варианты из ответа LLM проверяются Stockfish перед показом.
        </span>
      </footer>
    </div>
  );
}

function errorMessage(cause: unknown): string {
  if (cause instanceof ApiError) {
    return cause.code ? `${cause.message} (${cause.code})` : cause.message;
  }
  return cause instanceof Error ? cause.message : "Неизвестная ошибка";
}

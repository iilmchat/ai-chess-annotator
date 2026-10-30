import { type ChangeEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { analyzeGame, ApiError } from "../api";
import { AnalysisWorkspace } from "../components/AnalysisWorkspace";
import { DEMO_PGN, positionsFromPgn } from "../lib/pgn";
import {
  pgnFileErrorMessageKey,
  readPgnFile,
  type PgnFileError,
} from "../lib/pgnFile";
import { useAnalysisStore } from "../stores/analysisStore";
import type { Level } from "../types";

export default function HomePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
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
  const fileInputRef = useRef<HTMLInputElement>(null);

  /**
   * T-071: прочитать выбранный PGN-файл и подставить текст в редактор.
   * Уровень анализа не меняется, ошибка прошлого действия сбрасывается.
   */
  async function loadPgnFile(file: File) {
    setError(null);
    try {
      const text = await readPgnFile(file);
      setPgn(text);
    } catch (cause) {
      setError(t(pgnFileErrorMessageKey(asPgnFileError(cause))));
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Сброс значения позволяет повторно выбрать тот же самый файл.
    event.target.value = "";
    if (!file) {
      return;
    }
    void loadPgnFile(file);
  }

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
      navigate(`/analysis/${result.analysis_id}`);
    } catch (cause) {
      // KI-055: даже если AuthGate уже показывает экран подтверждения,
      // 403 от API должен читаться как понятная инструкция, а не код.
      if (cause instanceof ApiError && cause.code === "email_not_verified") {
        setError(t("auth.emailNotVerifiedShort"));
      } else {
        setError(errorMessage(cause, t("common.unknown")));
      }
    } finally {
      setBusy(false);
    }
  }

  const pgnPanel = (
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
      <input
        ref={fileInputRef}
        type="file"
        accept=".pgn,application/x-chess-pgn,text/plain"
        data-testid="pgn-file-input"
        onChange={handleFileChange}
        hidden
      />
      <div className="actions">
        <button
          type="button"
          className="button"
          data-testid="pgn-upload"
          aria-label={t("pgn.upload")}
          onClick={() => fileInputRef.current?.click()}
        >
          <span aria-hidden="true">📎</span> {t("pgn.upload")}
        </button>
        <span className="muted">{t("pgn.uploadHint")}</span>
      </div>
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
  );

  return (
    <>
      {error ? (
        <div className="banner banner--error" role="alert">
          {error}
        </div>
      ) : null}
      <AnalysisWorkspace
        analysis={analysis}
        positions={positions}
        initialFen={initialFen}
        selectedPly={selectedPly}
        level={level}
        onSelectPly={setSelectedPly}
        leftPanel={pgnPanel}
        busy={busy}
      />
    </>
  );
}

function errorMessage(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) {
    return cause.code ? `${cause.message} (${cause.code})` : cause.message;
  }
  return cause instanceof Error ? cause.message : fallback;
}

/** `readPgnFile` реджектит строкой-причиной — сужаем `unknown` до PgnFileError. */
function asPgnFileError(cause: unknown): PgnFileError {
  return cause === "too_large" ||
    cause === "wrong_type" ||
    cause === "empty" ||
    cause === "read_error"
    ? cause
    : "read_error";
}

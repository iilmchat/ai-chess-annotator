import { useEffect, useState } from "react";

import {
  ApiError,
  generateCommentary,
  updateCommentary,
} from "../api";
import { formatTokens, levelLabel } from "../lib/format";
import type { CommentaryResponse, Level } from "../types";

interface CommentaryPanelProps {
  analysisId: string | null;
  level: Level;
}

export function CommentaryPanel({ analysisId, level }: CommentaryPanelProps) {
  const [commentary, setCommentary] = useState<CommentaryResponse | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedEdit, setSavedEdit] = useState<number | null>(null);

  useEffect(() => {
    setCommentary(null);
    setDraft("");
    setEditing(false);
    setError(null);
    setSavedEdit(null);
  }, [analysisId, level]);

  async function handleGenerate() {
    if (!analysisId) {
      return;
    }
    setBusy(true);
    setError(null);
    setSavedEdit(null);
    try {
      const result = await generateCommentary(analysisId, level);
      setCommentary(result);
      setDraft(result.text);
      setEditing(false);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    if (!commentary || !draft.trim()) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await updateCommentary(commentary.commentary_id, draft);
      setCommentary({ ...commentary, text: result.text });
      setDraft(result.text);
      setEditing(false);
      setSavedEdit(result.edit_id);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-label="Комментарий">
      <div className="panel__header">
        <div>
          <h2>Комментарий</h2>
          <span className="muted">Уровень: {levelLabel(level)}</span>
        </div>
        <button
          type="button"
          className="button button--primary"
          onClick={handleGenerate}
          disabled={!analysisId || busy}
          aria-busy={busy}
        >
          {busy ? "Генерирую…" : commentary ? "Сгенерировать заново" : "Сгенерировать"}
        </button>
      </div>

      {!analysisId ? (
        <p className="empty">Сначала разберите партию — комментарий строится по её анализу.</p>
      ) : null}

      {error ? <p className="error">{error}</p> : null}

      {commentary ? (
        <>
          <div className="meta-row">
            <span>{commentary.model}</span>
            <span>{formatTokens(commentary.tokens_used)}</span>
            <span>{commentary.validated ? "валидирован" : "без валидации"}</span>
            <span>попыток: {commentary.attempts}</span>
            {savedEdit ? <span className="ok">edit #{savedEdit}</span> : null}
          </div>

          {editing ? (
            <>
              <textarea
                className="commentary-editor"
                value={draft}
                maxLength={20000}
                onChange={(event) => setDraft(event.target.value)}
                aria-label="Текст комментария"
              />
              <div className="actions">
                <button
                  type="button"
                  className="button button--primary"
                  onClick={handleSave}
                  disabled={busy || !draft.trim()}
                >
                  Сохранить правку
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    setDraft(commentary.text);
                    setEditing(false);
                  }}
                  disabled={busy}
                >
                  Отмена
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="commentary-text">{commentary.text}</div>
              <div className="actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    setDraft(commentary.text);
                    setEditing(true);
                  }}
                >
                  Редактировать
                </button>
              </div>
            </>
          )}
        </>
      ) : analysisId ? (
        <p className="empty">
          Нажмите «Сгенерировать» — комментарий появится здесь через несколько
          секунд (локальная модель может думать до минуты).
        </p>
      ) : null}
    </section>
  );
}

function errorMessage(cause: unknown): string {
  if (cause instanceof ApiError) {
    return cause.code ? `${cause.message} (${cause.code})` : cause.message;
  }
  return cause instanceof Error ? cause.message : "Неизвестная ошибка";
}

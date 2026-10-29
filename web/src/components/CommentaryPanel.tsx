import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, downloadCommentaryPdf, streamCommentary, updateCommentary } from "../api";
import { useTypewriter } from "../hooks/useTypewriter";
import type { CommentaryResponse, Level } from "../types";

interface CommentaryPanelProps {
  analysisId: string | null;
  level: Level;
}

export function CommentaryPanel({ analysisId, level }: CommentaryPanelProps) {
  const { t, i18n } = useTranslation();
  const [commentary, setCommentary] = useState<CommentaryResponse | null>(null);
  const [streamText, setStreamText] = useState("");
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedEdit, setSavedEdit] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const typed = useTypewriter(streamText);

  useEffect(() => {
    setCommentary(null);
    setStreamText("");
    setDraft("");
    setEditing(false);
    setError(null);
    setSavedEdit(null);
    abortRef.current?.abort();
    abortRef.current = null;
  }, [analysisId, level]);

  async function handleGenerate() {
    if (!analysisId) {
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError(null);
    setCommentary(null);
    setStreamText("");
    setDraft("");
    setEditing(false);
    setSavedEdit(null);
    let full = "";
    try {
      const done = await streamCommentary(
        analysisId,
        level,
        {
          signal: controller.signal,
          onDelta: (text) => {
            full += text;
            setStreamText(full);
          },
        },
        i18n.language,
      );
      setCommentary({
        commentary_id: done.commentary_id ?? 0,
        text: full,
        tokens_used: done.tokens_used,
        validated: done.validated,
        model: done.model ?? "",
        attempts: 1,
      });
      setDraft(full);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        setError(t("commentary.stopped"));
      } else {
        setError(errorMessage(cause, t("common.unknown")));
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  }

  function handleStop() {
    abortRef.current?.abort();
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
      setError(errorMessage(cause, t("common.unknown")));
    } finally {
      setBusy(false);
    }
  }

  async function handleDownloadPdf() {
    if (!commentary) {
      return;
    }
    setPdfBusy(true);
    setError(null);
    try {
      await downloadCommentaryPdf(commentary.commentary_id);
    } catch (cause) {
      setError(errorMessage(cause, t("common.unknown")));
    } finally {
      setPdfBusy(false);
    }
  }

  return (
    <section className="panel" aria-label={t("commentary.title")}>
      <div className="panel__header">
        <div>
          <h2>{t("commentary.title")}</h2>
          <span className="muted">
            {t("commentary.level", { level: t(`level.${level}`) })}
          </span>
        </div>
        <div className="actions actions--inline">
          {busy ? (
            <button type="button" className="button" onClick={handleStop}>
              {t("commentary.stop")}
            </button>
          ) : (
            <button
              type="button"
              className="button button--primary"
              onClick={handleGenerate}
              disabled={!analysisId || busy}
              aria-busy={busy}
            >
              {commentary ? t("commentary.regenerate") : t("commentary.generate")}
            </button>
          )}
        </div>
      </div>

      {!analysisId ? <p className="empty">{t("commentary.needAnalysis")}</p> : null}
      {error ? <p className="error">{error}</p> : null}

      {busy ? (
        <>
          <div className="commentary-text">
            {typed || t("commentary.generating")}
            <span className="caret" />
          </div>
        </>
      ) : commentary ? (
        <>
          <div className="meta-row">
            <span>{commentary.model}</span>
            <span>{t("common.tokens", { count: commentary.tokens_used })}</span>
            <span>
              {commentary.validated
                ? t("commentary.validated")
                : t("commentary.notValidated")}
            </span>
            {savedEdit ? (
              <span className="ok">{t("commentary.savedEdit", { id: savedEdit })}</span>
            ) : null}
          </div>

          {editing ? (
            <>
              <textarea
                className="commentary-editor"
                value={draft}
                maxLength={20000}
                onChange={(event) => setDraft(event.target.value)}
                aria-label={t("commentary.editorLabel")}
              />
              <div className="actions">
                <button
                  type="button"
                  className="button button--primary"
                  onClick={handleSave}
                  disabled={busy || !draft.trim()}
                >
                  {t("commentary.save")}
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
                  {t("commentary.cancel")}
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
                  {t("commentary.edit")}
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={handleDownloadPdf}
                  disabled={pdfBusy}
                  aria-busy={pdfBusy}
                >
                  {pdfBusy
                    ? t("commentary.pdfPreparing")
                    : t("commentary.downloadPdf")}
                </button>
              </div>
            </>
          )}
        </>
      ) : analysisId ? (
        <p className="empty">{t("commentary.empty")}</p>
      ) : null}
    </section>
  );
}

function errorMessage(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) {
    return cause.code ? `${cause.message} (${cause.code})` : cause.message;
  }
  return cause instanceof Error ? cause.message : fallback;
}

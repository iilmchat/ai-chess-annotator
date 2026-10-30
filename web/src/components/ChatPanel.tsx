import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, streamChat } from "../api";
import { useTypewriter } from "../hooks/useTypewriter";
import {
  conversationKey,
  useChatStore,
} from "../stores/chatStore";
import type { Level } from "../types";

interface ChatPanelProps {
  analysisId: string | null;
  level: Level;
}

export function ChatPanel({ analysisId, level }: ChatPanelProps) {
  const { t, i18n } = useTranslation();
  const key = conversationKey(analysisId, level);
  const messages = useChatStore((state) => state.messages[key]);
  const appendMessage = useChatStore((state) => state.append);
  const clearMessages = useChatStore((state) => state.clear);
  const visibleMessages = messages ?? [];
  const [question, setQuestion] = useState("");
  const [streamText, setStreamText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const typed = useTypewriter(streamText);
  const suggestions = [
    t("chat.suggestions.players"),
    t("chat.suggestions.mistake"),
    t("chat.suggestions.better"),
  ];

  useEffect(() => {
    setQuestion("");
    setStreamText("");
    setError(null);
    abortRef.current?.abort();
    abortRef.current = null;
  }, [analysisId, level]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [visibleMessages, streamText, busy]);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!analysisId || !trimmed || busy || !key) {
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const history = visibleMessages.map((message) => ({
      role: message.role,
      content: message.text,
    }));
    appendMessage(key, { role: "user", text: trimmed });
    setQuestion("");
    setStreamText("");
    setBusy(true);
    setError(null);
    let full = "";
    try {
      const done = await streamChat(
        analysisId,
        level,
        trimmed,
        {
          signal: controller.signal,
          onDelta: (chunk) => {
            full += chunk;
            setStreamText(full);
          },
        },
        history,
        i18n.language,
      );
      appendMessage(key, {
        role: "assistant",
        text: full,
        tokens: done.tokens_used,
      });
      setStreamText("");
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        setError(t("chat.stopped"));
      } else {
        setError(errorMessage(cause, t("common.unknown")));
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  return (
    <section className="panel" aria-label={t("chat.title")}>
      <div className="panel__header">
        <div>
          <h2>{t("chat.title")}</h2>
          <span className="muted">
            {t("chat.level", { level: t(`level.${level}`) })}
          </span>
        </div>
        {analysisId && visibleMessages.length > 0 ? (
          <button
            type="button"
            className="button button--ghost button--small"
            onClick={() => clearMessages(key)}
            disabled={busy}
          >
            {t("chat.clear")}
          </button>
        ) : null}
      </div>

      {!analysisId ? <p className="empty">{t("chat.needAnalysis")}</p> : null}

      <div className="chat" aria-live="polite">
        {visibleMessages.map((message, index) => (
          <div
            key={`${message.role}-${index}`}
            className={`chat__message chat__message--${message.role}`}
          >
            <p>{message.text}</p>
            {message.tokens ? (
              <span className="chat__meta">
                {t("common.tokens", { count: message.tokens })}
              </span>
            ) : null}
          </div>
        ))}
        {busy ? (
          <div className="chat__message chat__message--assistant">
            <p>
              {typed || t("chat.thinking")}
              <span className="caret" />
            </p>
          </div>
        ) : null}
        <div ref={endRef} />
      </div>

      {error ? <p className="error">{error}</p> : null}

      {analysisId && visibleMessages.length === 0 && !busy ? (
        <div className="suggestions">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="chip"
              onClick={() => void ask(suggestion)}
              disabled={busy}
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}

      <form
        className="chat__form"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={
            analysisId ? t("chat.placeholder") : t("chat.disabledPlaceholder")
          }
          maxLength={2000}
          disabled={!analysisId || busy}
          aria-label={t("chat.questionLabel")}
        />
        {busy ? (
          <button type="button" className="button" onClick={stop}>
            {t("chat.stop")}
          </button>
        ) : (
          <button
            type="submit"
            className="button button--primary"
            disabled={!analysisId || !question.trim()}
          >
            {t("chat.ask")}
          </button>
        )}
      </form>
    </section>
  );
}

function errorMessage(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) {
    return cause.code ? `${cause.message} (${cause.code})` : cause.message;
  }
  return cause instanceof Error ? cause.message : fallback;
}

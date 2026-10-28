import { useEffect, useRef, useState } from "react";

import { ApiError, askChat } from "../api";
import { formatTokens, levelLabel } from "../lib/format";
import type { Level } from "../types";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  tokens?: number;
}

interface ChatPanelProps {
  analysisId: string | null;
  level: Level;
}

const SUGGESTIONS = [
  "Кто играл белыми и чёрными?",
  "Какой ход был главной ошибкой?",
  "Что было лучше в ключевой момент?",
];

export function ChatPanel({ analysisId, level }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages([]);
    setQuestion("");
    setError(null);
  }, [analysisId, level]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, busy]);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!analysisId || !trimmed || busy) {
      return;
    }
    setMessages((current) => [...current, { role: "user", text: trimmed }]);
    setQuestion("");
    setBusy(true);
    setError(null);
    try {
      const result = await askChat(analysisId, level, trimmed);
      setMessages((current) => [
        ...current,
        { role: "assistant", text: result.reply, tokens: result.tokens_used },
      ]);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-label="Чат по партии">
      <div className="panel__header">
        <div>
          <h2>Чат по партии</h2>
          <span className="muted">Уровень: {levelLabel(level)}</span>
        </div>
      </div>

      {!analysisId ? (
        <p className="empty">Сначала разберите партию — чат отвечает по её анализу.</p>
      ) : null}

      <div className="chat" aria-live="polite">
        {messages.map((message, index) => (
          <div
            key={`${message.role}-${index}`}
            className={`chat__message chat__message--${message.role}`}
          >
            <p>{message.text}</p>
            {message.tokens ? (
              <span className="chat__meta">{formatTokens(message.tokens)}</span>
            ) : null}
          </div>
        ))}
        {busy ? <div className="chat__message chat__message--assistant">Думаю…</div> : null}
        <div ref={endRef} />
      </div>

      {error ? <p className="error">{error}</p> : null}

      {analysisId && messages.length === 0 ? (
        <div className="suggestions">
          {SUGGESTIONS.map((suggestion) => (
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
          placeholder={analysisId ? "Спросите про ход или план…" : "Сначала анализ"}
          maxLength={2000}
          disabled={!analysisId || busy}
          aria-label="Вопрос по партии"
        />
        <button
          type="submit"
          className="button button--primary"
          disabled={!analysisId || busy || !question.trim()}
        >
          Спросить
        </button>
      </form>
    </section>
  );
}

function errorMessage(cause: unknown): string {
  if (cause instanceof ApiError) {
    return cause.code ? `${cause.message} (${cause.code})` : cause.message;
  }
  return cause instanceof Error ? cause.message : "Неизвестная ошибка";
}

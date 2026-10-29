import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { forgotPassword } from "../api";

/**
 * `/forgot-password` — запрос письма со ссылкой сброса пароля (KI-055).
 *
 * Backend всегда отвечает 202 (не раскрывает существование email), поэтому
 * после успешного запроса показываем нейтральное сообщение независимо от
 * того, зарегистрирован адрес или нет.
 */
export default function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedEmail = email.trim();
    if (!normalizedEmail) {
      setError(t("forgotPassword.required"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await forgotPassword(normalizedEmail);
      setSent(true);
    } catch {
      // Сеть/429 не должны раскрывать, существует ли email: сообщение
      // общее и не зависит от адреса.
      setError(t("forgotPassword.genericError"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="panel auth-card">
        <h1 className="auth-card__title">{t("forgotPassword.title")}</h1>
        <p className="muted auth-card__subtitle">
          {t("forgotPassword.subtitle")}
        </p>

        {sent ? (
          <div className="auth-status" role="status">
            <p className="auth-status__title">
              {t("forgotPassword.sentTitle")}
            </p>
            <p className="muted">{t("forgotPassword.sentBody")}</p>
            <div className="actions auth-status__actions">
              <Link to="/login" className="button button--primary">
                {t("forgotPassword.backToLogin")}
              </Link>
            </div>
          </div>
        ) : (
          <form className="auth-form" onSubmit={handleSubmit} noValidate>
            <label className="field">
              <span>{t("auth.email")}</span>
              <input
                className="auth-input"
                type="email"
                name="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={submitting}
                placeholder="you@example.com"
              />
            </label>

            {error ? (
              <div className="error auth-error" role="alert">
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              className="button button--primary button--wide auth-submit"
              disabled={submitting}
              aria-busy={submitting}
            >
              {submitting
                ? t("forgotPassword.submitting")
                : t("forgotPassword.submit")}
            </button>

            <div className="actions auth-links">
              <Link to="/login" className="auth-link">
                {t("forgotPassword.backToLogin")}
              </Link>
            </div>
          </form>
        )}
      </section>
    </main>
  );
}

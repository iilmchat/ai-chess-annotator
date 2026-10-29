import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, resetPassword } from "../api";
import { useAuthStore } from "../stores/authStore";

/**
 * `/reset-password?token=…` — новый пароль по ссылке из письма (KI-055).
 *
 * Токен одноразовый: после успеха backend отзывает все refresh-сессии и
 * очищает cookie, поэтому редиректим на `/login` с сообщением об успехе.
 */
export default function ResetPasswordPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const clearSession = useAuthStore((state) => state.clearSession);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) {
      setError(t("resetPassword.missingToken"));
      return;
    }
    if (password.length < 8) {
      setError(t("resetPassword.weakPassword"));
      return;
    }
    if (password !== confirmation) {
      setError(t("resetPassword.mismatch"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await resetPassword(token, password);
      // Backend отозвал все refresh и очистил cookie; локальную сессию
      // (access в памяти) тоже нужно сбросить, иначе шапка покажет старого
      // пользователя, а форма входа — нет.
      clearSession();
      navigate("/login", {
        replace: true,
        state: { passwordReset: true },
      });
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.status === 401
          ? t("resetPassword.invalidToken")
          : cause instanceof Error && cause.message
            ? cause.message
            : t("common.unknown"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <main className="auth-page">
        <section className="panel auth-card">
          <h1 className="auth-card__title">{t("resetPassword.title")}</h1>
          <div className="auth-status" role="alert">
            <p className="auth-status__title">
              {t("resetPassword.errorTitle")}
            </p>
            <p className="muted">{t("resetPassword.missingToken")}</p>
            <div className="actions auth-status__actions">
              <Link to="/forgot-password" className="button button--primary">
                {t("resetPassword.goForgot")}
              </Link>
              <Link to="/login" className="button">
                {t("resetPassword.backToLogin")}
              </Link>
            </div>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <section className="panel auth-card">
        <h1 className="auth-card__title">{t("resetPassword.title")}</h1>
        <p className="muted auth-card__subtitle">
          {t("resetPassword.subtitle")}
        </p>

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <label className="field">
            <span>{t("resetPassword.newPassword")}</span>
            <input
              className="auth-input"
              type="password"
              name="new-password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={submitting}
            />
          </label>
          <label className="field">
            <span>{t("resetPassword.confirmPassword")}</span>
            <input
              className="auth-input"
              type="password"
              name="confirm-password"
              autoComplete="new-password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              disabled={submitting}
            />
          </label>
          <p className="hint auth-hint">{t("resetPassword.weakPassword")}</p>

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
              ? t("resetPassword.submitting")
              : t("resetPassword.submit")}
          </button>

          <div className="actions auth-links">
            <Link to="/login" className="auth-link">
              {t("resetPassword.backToLogin")}
            </Link>
          </div>
        </form>
      </section>
    </main>
  );
}

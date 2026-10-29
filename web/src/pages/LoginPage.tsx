import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError, loginUser, registerUser } from "../api";

type Mode = "login" | "register";

interface LoginLocationState {
  from?: { pathname?: string };
}

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function changeMode(next: Mode) {
    if (next === mode) {
      return;
    }
    setMode(next);
    setError(null);
  }

  function errorMessage(cause: unknown): string {
    if (cause instanceof ApiError) {
      switch (cause.code) {
        case "invalid_credentials":
        case "unauthorized":
          return t("auth.invalidCredentials");
        case "email_taken":
          return t("auth.emailTaken");
        case "inactive":
        case "inactive_user":
        case "user_inactive":
          return t("auth.inactive");
        case "session_expired":
          return t("auth.sessionExpired");
        default:
          break;
      }
      if (
        cause.status === 403 &&
        /inactive|disabled|отключ/i.test(cause.message)
      ) {
        return t("auth.inactive");
      }
      // Fallback — detail из ответа API.
      return cause.message || t("common.unknown");
    }
    return cause instanceof Error && cause.message
      ? cause.message
      : t("common.unknown");
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedEmail = email.trim();
    if (!normalizedEmail || !password) {
      setError(t("auth.required"));
      return;
    }
    if (mode === "register" && password.length < 8) {
      setError(t("auth.weakPassword"));
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      if (mode === "register") {
        await registerUser(normalizedEmail, password);
      }
      await loginUser(normalizedEmail, password);
      const state = location.state as LoginLocationState | null;
      navigate(state?.from?.pathname ?? "/", { replace: true });
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSubmitting(false);
    }
  }

  const submitLabel =
    mode === "register" ? t("auth.register") : t("auth.login");

  return (
    <main className="auth-page">
      <section className="panel auth-card">
        <h1 className="auth-card__title">{t("auth.title")}</h1>
        <p className="muted auth-card__subtitle">{t("auth.subtitle")}</p>

        <div className="auth-tabs" role="tablist" aria-label={t("auth.title")}>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={`auth-tab ${mode === "login" ? "auth-tab--active" : ""}`}
            onClick={() => changeMode("login")}
            disabled={submitting}
          >
            {t("auth.loginTab")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            className={`auth-tab ${
              mode === "register" ? "auth-tab--active" : ""
            }`}
            onClick={() => changeMode("register")}
            disabled={submitting}
          >
            {t("auth.registerTab")}
          </button>
        </div>

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
          <label className="field">
            <span>{t("auth.password")}</span>
            <input
              className="auth-input"
              type="password"
              name="password"
              autoComplete={
                mode === "register" ? "new-password" : "current-password"
              }
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={submitting}
            />
          </label>
          {mode === "register" ? (
            <p className="hint auth-hint">{t("auth.passwordHint")}</p>
          ) : null}

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
            {submitting ? t("auth.submitting") : submitLabel}
          </button>
        </form>
      </section>
    </main>
  );
}

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError, getMe, verifyEmail } from "../api";
import { getAuthState, useAuthStore } from "../stores/authStore";
import type { EmailVerificationResponse } from "../types";

type Status = "pending" | "success" | "error";
type ErrorKind = "missing" | "invalid" | "network";

/**
 * Один запрос на токен: StrictMode в dev монтирует эффект дважды, а
 * подтверждение не должно уходить дважды. При ошибке промис удаляется,
 * чтобы кнопка «Повторить» создала новый запрос.
 */
const verificationRequests = new Map<
  string,
  Promise<EmailVerificationResponse>
>();

function verifyOnce(token: string): Promise<EmailVerificationResponse> {
  const existing = verificationRequests.get(token);
  if (existing) {
    return existing;
  }
  const request = verifyEmail(token);
  verificationRequests.set(token, request);
  const reset = () => {
    if (verificationRequests.get(token) === request) {
      verificationRequests.delete(token);
    }
  };
  request.then(reset, reset);
  return request;
}

/**
 * `/verify-email?token=…` — подтверждение email по ссылке из письма (KI-055).
 *
 * Публичная страница: доступна без сессии (ссылка может открыться в другом
 * браузере). Если пользователь уже авторизован, после успеха обновляем
 * профиль в `authStore`, иначе `AuthGate` продолжил бы показывать экран
 * «подтвердите email».
 */
export default function VerifyEmailPage() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const setUser = useAuthStore((state) => state.setUser);
  const [status, setStatus] = useState<Status>(token ? "pending" : "error");
  const [errorKind, setErrorKind] = useState<ErrorKind>("missing");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setErrorKind("missing");
      return;
    }
    let active = true;
    setStatus("pending");
    verifyOnce(token)
      .then(async (result) => {
        if (!active) {
          return;
        }
        if (result.is_verified && getAuthState().accessToken) {
          try {
            const profile = await getMe();
            if (active) {
              setUser(profile);
            }
          } catch {
            // Профиль обновится при следующем входе; подтверждение уже есть.
          }
        }
        if (active) {
          setStatus("success");
        }
      })
      .catch((cause: unknown) => {
        if (!active) {
          return;
        }
        setErrorKind(
          cause instanceof ApiError && cause.status === 401
            ? "invalid"
            : "network",
        );
        setStatus("error");
      });
    return () => {
      active = false;
    };
  }, [token, attempt, setUser]);

  const errorMessage =
    errorKind === "missing"
      ? t("verifyEmail.missingToken")
      : errorKind === "invalid"
        ? t("verifyEmail.invalidToken")
        : t("verifyEmail.networkError");

  return (
    <main className="auth-page">
      <section className="panel auth-card" aria-live="polite">
        <h1 className="auth-card__title">{t("verifyEmail.title")}</h1>

        {status === "pending" ? (
          <div className="auth-status" role="status">
            <span className="auth-spinner" aria-hidden="true" />
            <p className="muted">{t("verifyEmail.checking")}</p>
          </div>
        ) : null}

        {status === "success" ? (
          <div className="auth-status" role="status">
            <p className="auth-status__title">
              {t("verifyEmail.successTitle")}
            </p>
            <p className="muted">{t("verifyEmail.successBody")}</p>
            <div className="actions auth-status__actions">
              <Link to="/" className="button button--primary">
                {t("verifyEmail.goHome")}
              </Link>
              <Link to="/login" className="button">
                {t("verifyEmail.goLogin")}
              </Link>
            </div>
          </div>
        ) : null}

        {status === "error" ? (
          <div className="auth-status" role="alert">
            <p className="auth-status__title">{t("verifyEmail.errorTitle")}</p>
            <p className="muted">{errorMessage}</p>
            <div className="actions auth-status__actions">
              {errorKind === "network" ? (
                <button
                  type="button"
                  className="button button--primary"
                  onClick={() => setAttempt((value) => value + 1)}
                >
                  {t("verifyEmail.retry")}
                </button>
              ) : null}
              <Link to="/login" className="button">
                {t("verifyEmail.goLogin")}
              </Link>
            </div>
          </div>
        ) : null}
      </section>
    </main>
  );
}

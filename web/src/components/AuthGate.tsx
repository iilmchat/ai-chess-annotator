import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError, getMe } from "../api";
import { selectIsAuthenticated, useAuthStore } from "../stores/authStore";
import type { AuthUser } from "../types";

/**
 * Профиль запрашиваем один раз на токен: React.StrictMode в dev запускает
 * эффект дважды, а параллельные монтирования не должны дублировать `/auth/me`.
 */
let profileRequest: Promise<AuthUser> | null = null;
let profileRequestToken: string | null = null;

function loadProfileOnce(accessToken: string): Promise<AuthUser> {
  if (profileRequest && profileRequestToken === accessToken) {
    return profileRequest;
  }
  const request = getMe();
  profileRequest = request;
  profileRequestToken = accessToken;
  const reset = () => {
    if (profileRequest === request) {
      profileRequest = null;
      profileRequestToken = null;
    }
  };
  request.then(reset, reset);
  return request;
}

interface AuthGateProps {
  children: ReactNode;
}

/**
 * Защищает приватные маршруты: без access-токена отправляет на `/login`,
 * а если токен есть, но профиль ещё не загружен — восстанавливает его.
 * Пока идёт bootstrap по refresh-cookie (`initializing`), показываем loading,
 * иначе была бы ложная перезагрузка на /login.
 */
export function AuthGate({ children }: AuthGateProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const accessToken = useAuthStore((state) => state.accessToken);
  const user = useAuthStore((state) => state.user);
  const initializing = useAuthStore((state) => state.initializing);
  const setUser = useAuthStore((state) => state.setUser);
  const clearSession = useAuthStore((state) => state.clearSession);
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [checkingVerification, setCheckingVerification] = useState(false);

  const needsProfile = Boolean(accessToken) && !user;
  //: KI-041/KI-055: генерация доступна только после подтверждения email;
  //: admin/auditor — служебные роли, backend их пропускает без флага.
  const isUnverified = Boolean(
    user && user.role === "user" && !user.is_verified,
  );

  useEffect(() => {
    if (initializing || !accessToken || user) {
      return;
    }
    let active = true;
    setError(null);
    loadProfileOnce(accessToken)
      .then((profile) => {
        if (active) {
          setUser(profile);
        }
      })
      .catch((cause: unknown) => {
        if (!active) {
          return;
        }
        if (cause instanceof ApiError && cause.status === 401) {
          clearSession();
          return;
        }
        setError(
          cause instanceof Error && cause.message
            ? cause.message
            : t("common.unknown"),
        );
      });
    return () => {
      active = false;
    };
  }, [initializing, accessToken, user, attempt, setUser, clearSession, t]);

  async function refreshVerification() {
    setCheckingVerification(true);
    setError(null);
    try {
      const profile = await getMe();
      setUser(profile);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        clearSession();
        return;
      }
      setError(
        cause instanceof Error && cause.message
          ? cause.message
          : t("common.unknown"),
      );
    } finally {
      setCheckingVerification(false);
    }
  }

  if (initializing) {
    return (
      <main className="auth-gate">
        <p className="muted">{t("common.loading")}</p>
      </main>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (isUnverified && user) {
    return (
      <main className="auth-gate">
        <section className="panel auth-card auth-gate__card">
          <h1 className="auth-card__title">
            {t("auth.emailNotVerifiedTitle")}
          </h1>
          <p className="muted auth-card__subtitle">
            {t("auth.emailNotVerifiedBody", { email: user.email })}
          </p>
          <p className="hint">{t("auth.emailNotVerifiedHint")}</p>
          {error ? (
            <div className="auth-error" role="alert">
              {error}
            </div>
          ) : null}
          <div className="actions auth-gate__actions">
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                void refreshVerification();
              }}
              disabled={checkingVerification}
              aria-busy={checkingVerification}
            >
              {checkingVerification
                ? t("common.loading")
                : t("auth.emailNotVerifiedRefresh")}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                clearSession();
                navigate("/login", { replace: true });
              }}
            >
              {t("auth.logout")}
            </button>
          </div>
        </section>
      </main>
    );
  }

  if (needsProfile) {
    return (
      <main className="auth-gate">
        {error ? (
          <div className="auth-gate__card">
            <div className="banner banner--error" role="alert">
              {error}
            </div>
            <div className="actions">
              <button
                type="button"
                className="button button--primary"
                onClick={() => setAttempt((value) => value + 1)}
              >
                {t("common.retry")}
              </button>
            </div>
          </div>
        ) : (
          <p className="muted">{t("common.loading")}</p>
        )}
      </main>
    );
  }

  return <>{children}</>;
}

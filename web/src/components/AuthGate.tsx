import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation } from "react-router-dom";

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
 */
export function AuthGate({ children }: AuthGateProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const accessToken = useAuthStore((state) => state.accessToken);
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);
  const clearSession = useAuthStore((state) => state.clearSession);
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const needsProfile = Boolean(accessToken) && !user;

  useEffect(() => {
    if (!accessToken || user) {
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
  }, [accessToken, user, attempt, setUser, clearSession, t]);

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
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

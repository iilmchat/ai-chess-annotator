import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { ApiError, getMe, listGames } from "../api";
import { Skeleton } from "../components/Skeleton";
import { useAuthStore } from "../stores/authStore";
import type { GameListItem } from "../types";

export default function HistoryPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);
  const clearSession = useAuthStore((state) => state.clearSession);
  const [games, setGames] = useState<GameListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checkingVerification, setCheckingVerification] = useState(false);
  const [verificationChecked, setVerificationChecked] = useState(false);

  //: Итерация 35: история недоступна до подтверждения email. Запрос партий
  //: намеренно не отправляется, чтобы экран не намекал на наличие данных.
  const isUnverified = Boolean(
    user && user.role === "user" && !user.is_verified,
  );

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listGames()
      .then((items) => setGames(items))
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : t("common.unknown")),
      )
      .finally(() => setLoading(false));
  }, [t]);

  useEffect(() => {
    // Неподтверждённому пользователю список партий не запрашиваем; когда он
    // подтвердит email (setUser → isUnverified станет false), эффект загрузит
    // историю автоматически.
    if (isUnverified) {
      return;
    }
    load();
  }, [isUnverified, load]);

  function signOut() {
    clearSession();
    navigate("/login", { replace: true });
  }

  async function refreshVerification() {
    setCheckingVerification(true);
    setError(null);
    setVerificationChecked(false);
    try {
      const profile = await getMe();
      setUser(profile);
      if (profile.role === "user" && !profile.is_verified) {
        setVerificationChecked(true);
      }
      // Если профиль стал подтверждённым, эффект выше сам загрузит историю.
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        clearSession();
        navigate("/login", { replace: true });
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

  if (isUnverified && user) {
    return (
      <main className="page">
        <section className="panel empty">
          <h1 className="auth-card__title">
            {t("history.emailNotVerifiedTitle")}
          </h1>
          <p className="muted auth-card__subtitle">
            {t("history.emailNotVerifiedBody", { email: user.email })}
          </p>
          <p className="hint">{t("history.emailNotVerifiedHint")}</p>
          {error ? (
            <div className="banner banner--error" role="alert">
              {error}
            </div>
          ) : null}
          {verificationChecked && !error ? (
            <p className="muted" role="status">
              {t("history.emailNotVerifiedStillUnverified")}
            </p>
          ) : null}
          <div className="actions">
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
                : t("history.emailNotVerifiedCheck")}
            </button>
            <button type="button" className="button" onClick={signOut}>
              {t("history.emailNotVerifiedLogout")}
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="page">
      <div className="page__head">
        <div>
          <h1>{t("history.title")}</h1>
          <p className="muted">{t("history.subtitle")}</p>
        </div>
        <button
          type="button"
          className="button"
          onClick={load}
          disabled={loading}
        >
          {t("history.refresh")}
        </button>
      </div>

      {error ? (
        <div className="banner banner--error" role="alert">
          {error}
        </div>
      ) : null}

      {loading ? (
        <section className="panel">
          <Skeleton lines={7} />
        </section>
      ) : games.length === 0 ? (
        <section className="panel empty">{t("history.empty")}</section>
      ) : (
        <section className="panel history">
          <div className="history__scroll">
            <table className="history__table">
              <thead>
                <tr>
                  <th>{t("history.date")}</th>
                  <th>{t("history.white")}</th>
                  <th>{t("history.black")}</th>
                  <th>{t("history.event")}</th>
                  <th>{t("history.level")}</th>
                  <th aria-label={t("history.open")} />
                </tr>
              </thead>
              <tbody>
                {games.map((game) => (
                  <tr
                    key={game.id}
                    role="link"
                    tabIndex={0}
                    onClick={() => navigate(`/analysis/${game.id}`)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        navigate(`/analysis/${game.id}`);
                      }
                    }}
                  >
                    <td className="muted">
                      {new Date(game.created_at).toLocaleString()}
                    </td>
                    <td>{game.white ?? "—"}</td>
                    <td>{game.black ?? "—"}</td>
                    <td>{game.event ?? "—"}</td>
                    <td>
                      <span className="pill">
                        {t(`level.${game.level}`)}
                      </span>
                    </td>
                    <td className="history__arrow">→</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}

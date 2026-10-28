import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { listGames } from "../api";
import { Skeleton } from "../components/Skeleton";
import type { GameListItem } from "../types";

export default function HistoryPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [games, setGames] = useState<GameListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    listGames()
      .then((items) => setGames(items))
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : t("common.unknown")),
      )
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

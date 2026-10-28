import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { getGame } from "../api";
import { AnalysisWorkspace } from "../components/AnalysisWorkspace";
import { AnalysisSkeleton } from "../components/Skeleton";
import type { PgnPosition } from "../lib/pgn";
import { useAnalysisStore } from "../stores/analysisStore";
import type { Level } from "../types";

export default function AnalysisPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const {
    analysis,
    positions,
    initialFen,
    selectedPly,
    level,
    setLevel,
    setAnalysis,
    setSelectedPly,
  } = useAnalysisStore();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) {
      setError(t("analysis.missingId"));
      setLoading(false);
      return;
    }
    if (analysis?.analysis_id === id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    getGame(id)
      .then((game) => {
        const nextLevel = (game.analysis_level ?? "club") as Level;
        const nextPositions: PgnPosition[] = game.moves.map((move) => ({
          ply: move.ply,
          san: move.san,
          uci: move.uci,
          fen: move.fen,
        }));
        setLevel(nextLevel);
        setAnalysis(game, nextPositions, game.initial_fen);
      })
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : t("common.unknown")),
      )
      .finally(() => setLoading(false));
  }, [
    id,
    analysis?.analysis_id,
    setAnalysis,
    setLevel,
    t,
  ]);

  if (loading) {
    return <AnalysisSkeleton />;
  }

  if (error || !analysis) {
    return (
      <main className="page">
        <div className="banner banner--error" role="alert">
          {error ?? t("analysis.notFound")}
        </div>
        <div className="actions">
          <button
            type="button"
            className="button"
            onClick={() => navigate("/history")}
          >
            {t("analysis.backToHistory")}
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={() => window.location.reload()}
          >
            {t("common.retry")}
          </button>
        </div>
      </main>
    );
  }

  const infoPanel = (
    <section className="panel">
      <div className="panel__header">
        <h2>{t("analysis.title")}</h2>
        <button
          type="button"
          className="button button--ghost button--small"
          onClick={() => navigate("/history")}
        >
          {t("analysis.backToHistory")}
        </button>
      </div>
      <p className="summary__players">
        {analysis.meta.white ?? "—"} — {analysis.meta.black ?? "—"}
      </p>
      <p className="muted">
        {t("analysis.level", { level: t(`level.${level}`) })}
        {analysis.created_at
          ? ` · ${new Date(analysis.created_at).toLocaleString()}`
          : ""}
      </p>
    </section>
  );

  return (
    <AnalysisWorkspace
      analysis={analysis}
      positions={positions}
      initialFen={initialFen}
      selectedPly={selectedPly}
      level={level}
      onSelectPly={setSelectedPly}
      leftPanel={infoPanel}
    />
  );
}

import { useTranslation } from "react-i18next";

interface NavigationControlsProps {
  selectedPly: number;
  totalPlies: number;
  onSelectPly: (ply: number) => void;
}

/** Прижать полуход к допустимому диапазону [0, total]. */
function clampPly(ply: number, total: number): number {
  return Math.min(total, Math.max(0, ply));
}

/**
 * Навигация по полуходам: в начало / назад / вперёд / в конец.
 * Кнопки на границах диапазона отключены.
 */
export function NavigationControls({
  selectedPly,
  totalPlies,
  onSelectPly,
}: NavigationControlsProps) {
  const { t } = useTranslation();
  const total = Math.max(0, totalPlies);
  const current = clampPly(selectedPly, total);
  const goTo = (ply: number) => () => onSelectPly(clampPly(ply, total));

  return (
    <div
      className="nav-controls"
      role="group"
      aria-label={t("analysis.nav.label")}
    >
      <button
        type="button"
        className="nav-controls__button"
        data-testid="nav-start"
        aria-label={t("analysis.nav.toStart")}
        title={t("analysis.nav.toStart")}
        disabled={current <= 0}
        onClick={goTo(0)}
      >
        <span aria-hidden="true">⏮</span>
      </button>
      <button
        type="button"
        className="nav-controls__button"
        data-testid="nav-prev"
        aria-label={t("analysis.nav.prev")}
        title={t("analysis.nav.prev")}
        disabled={current <= 0}
        onClick={goTo(current - 1)}
      >
        <span aria-hidden="true">◀</span>
      </button>
      <span className="nav-controls__counter" aria-live="polite">
        {current} / {total}
      </span>
      <button
        type="button"
        className="nav-controls__button"
        data-testid="nav-next"
        aria-label={t("analysis.nav.next")}
        title={t("analysis.nav.next")}
        disabled={current >= total}
        onClick={goTo(current + 1)}
      >
        <span aria-hidden="true">▶</span>
      </button>
      <button
        type="button"
        className="nav-controls__button"
        data-testid="nav-end"
        aria-label={t("analysis.nav.toEnd")}
        title={t("analysis.nav.toEnd")}
        disabled={current >= total}
        onClick={goTo(total)}
      >
        <span aria-hidden="true">⏭</span>
      </button>
    </div>
  );
}

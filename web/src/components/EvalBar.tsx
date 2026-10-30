import { useTranslation } from "react-i18next";

import { evalToWhiteShare, mateLabel } from "../lib/eval";

interface EvalBarProps {
  evalCp: number | null;
  mate: number | null;
}

/**
 * Вертикальная шкала оценки рядом с доской: сверху — перевес белых, снизу —
 * чёрных. Для мата шкала заливается целиком, а под ней появляется подпись
 * «M3»/«M-2».
 */
export function EvalBar({ evalCp, mate }: EvalBarProps) {
  const { t } = useTranslation();
  const share = evalToWhiteShare(evalCp, mate);
  const whitePercent = Math.round(share * 100);
  const badge = mateLabel(mate);

  return (
    <div className="eval-bar">
      <div
        className="eval-bar__track"
        role="meter"
        aria-label={t("analysis.evalBar.label")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={whitePercent}
        aria-valuetext={`${t("analysis.evalBar.white")} ${whitePercent}% / ${t(
          "analysis.evalBar.black",
        )} ${100 - whitePercent}%`}
        data-testid="eval-bar"
        data-white-share={share.toFixed(2)}
      >
        {/* Доля шкалы задаётся динамически, поэтому высота — inline-стиль. */}
        <div
          className="eval-bar__white"
          style={{ height: `${share * 100}%` }}
        />
        <div
          className="eval-bar__black"
          style={{ height: `${(1 - share) * 100}%` }}
        />
      </div>
      {badge !== null ? (
        <span className="eval-bar__badge" data-testid="eval-bar-mate">
          {badge}
        </span>
      ) : null}
    </div>
  );
}

import { useMemo, type MouseEvent as ReactMouseEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { evalSeries } from "../lib/eval";
import { formatEval, movePrefix } from "../lib/format";
import type { AnalyzedMove } from "../types";

interface EvalGraphProps {
  moves: AnalyzedMove[];
  selectedPly: number;
  onSelectPly: (ply: number) => void;
}

/** Высота графика в пикселях (ResponsiveContainer требует число). */
const CHART_HEIGHT = 190;
/** Отступы внутри SVG; нужны и для расчёта клика по координате. */
const CHART_MARGIN = { top: 8, right: 14, bottom: 18, left: 0 };
/** Ширина оси Y: от неё начинается область графика. */
const Y_AXIS_WIDTH = 44;
/** Высота оси X внизу: по ней ограничиваем клик областью графика. */
const X_AXIS_HEIGHT = 34;

/**
 * График оценки по полуходам (recharts).
 *
 * Клик по точке или ближайшему столбцу переключает доску на этот ход.
 * В recharts 3 `onClick` самого чарта отдаёт индекс из hover-состояния, которое
 * к моменту клика ещё не обновлено (mousemove приходит отдельным событием),
 * поэтому надёжнее считать ближайший полуход по координате клика: геометрия
 * области графика задана константами ниже.
 */
export function EvalGraph({ moves, selectedPly, onSelectPly }: EvalGraphProps) {
  const { t } = useTranslation();
  const data = useMemo(() => evalSeries(moves), [moves]);
  const maxPly = useMemo(
    () => data.reduce((max, point) => Math.max(max, point.ply), 1),
    [data],
  );
  const selectedPoint = useMemo(
    () => data.find((point) => point.ply === selectedPly) ?? null,
    [data, selectedPly],
  );

  const handleChartClick = (event: ReactMouseEvent<HTMLDivElement>): void => {
    if (data.length === 0) {
      return;
    }
    const surface = event.currentTarget.querySelector<SVGSVGElement>(
      ".recharts-surface",
    );
    if (!surface) {
      return;
    }
    const rect = surface.getBoundingClientRect();
    if (rect.width <= 0) {
      return;
    }
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const plotLeft = Y_AXIS_WIDTH;
    const plotRight = rect.width - CHART_MARGIN.right;
    const plotTop = CHART_MARGIN.top;
    const plotBottom = rect.height - CHART_MARGIN.bottom - X_AXIS_HEIGHT;
    // Клики вне области графика (оси, подписи) игнорируем.
    if (
      x < plotLeft ||
      x > plotRight ||
      y < plotTop ||
      y > plotBottom ||
      plotRight <= plotLeft
    ) {
      return;
    }
    const ratio = (x - plotLeft) / (plotRight - plotLeft);
    const targetPly = ratio * maxPly;
    const nearest = data.reduce((best, point) =>
      Math.abs(point.ply - targetPly) < Math.abs(best.ply - targetPly)
        ? point
        : best,
    );
    onSelectPly(nearest.ply);
  };

  return (
    <section
      className="panel eval-graph"
      aria-label={t("analysis.graph.title")}
      data-testid="eval-graph"
      data-selected-ply={selectedPly}
    >
      <div className="eval-graph__header">
        <h2>{t("analysis.graph.title")}</h2>
        <span className="muted">{t("analysis.graph.hint")}</span>
      </div>
      {data.length > 0 ? (
        <div
          className="eval-graph__chart"
          onClick={handleChartClick}
          data-testid="eval-graph-plot"
        >
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <LineChart
              data={data}
              margin={CHART_MARGIN}
              style={{ cursor: "crosshair" }}
            >
              <CartesianGrid
                stroke="rgba(36, 53, 83, 0.55)"
                strokeDasharray="3 3"
                vertical={false}
              />
              <XAxis
                dataKey="ply"
                type="number"
                domain={[0, maxPly]}
                allowDecimals={false}
                tickCount={Math.min(9, maxPly + 1)}
                height={X_AXIS_HEIGHT}
                tick={{ fontSize: 11 }}
                label={{
                  value: t("analysis.graph.axisPly"),
                  position: "insideBottom",
                  offset: -6,
                }}
              />
              <YAxis
                domain={[-1000, 1000]}
                ticks={[-1000, -500, 0, 500, 1000]}
                width={Y_AXIS_WIDTH}
                tick={{ fontSize: 11 }}
                label={{
                  value: t("analysis.graph.axisEval"),
                  angle: -90,
                  position: "insideLeft",
                  offset: 12,
                }}
              />
              <ReferenceLine y={0} className="eval-graph__zero" />
              {selectedPoint ? (
                <ReferenceLine
                  x={selectedPoint.ply}
                  className="eval-graph__selected"
                />
              ) : null}
              <Tooltip
                formatter={(value) => formatEval(Number(value), null)}
                labelFormatter={(label) =>
                  typeof label === "number"
                    ? movePrefix(label)
                    : String(label ?? "")
                }
              />
              <Line
                type="monotone"
                dataKey="value"
                name={t("analysis.graph.axisEval")}
                className="eval-graph__line"
                strokeWidth={2}
                dot={{ r: 3 }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
              />
              {selectedPoint ? (
                <ReferenceDot
                  x={selectedPoint.ply}
                  y={selectedPoint.value}
                  r={5}
                  className="eval-graph__selected-dot"
                />
              ) : null}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}
    </section>
  );
}

// Inline SVG sparkline. Renders `history` as a dashed line and `forecast`
// as a solid line, sharing the y-axis. Zero deps; ~80 LOC.

type Props = {
  history: string[];     // numeric strings
  forecast: string[];
  width?: number;
  height?: number;
  className?: string;
};

export function Sparkline({
  history, forecast, width = 240, height = 56, className = "",
}: Props) {
  const hVals = history.map(Number);
  const fVals = forecast.map(Number);
  const all = [...hVals, ...fVals];
  if (all.length < 2) {
    return <span className="opacity-50 text-xs">insufficient data</span>;
  }
  const max = Math.max(...all);
  const min = Math.min(...all);
  const span = max - min || 1;

  const pad = 4;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  const total = hVals.length + fVals.length;

  const x = (i: number) => pad + (i / (total - 1)) * innerW;
  const y = (v: number) => pad + (1 - (v - min) / span) * innerH;

  const histPoints = hVals.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const lastHistIdx = hVals.length - 1;
  // Forecast line continues from the last history point for visual continuity
  const fcstPoints = [
    `${x(lastHistIdx)},${y(hVals[lastHistIdx])}`,
    ...fVals.map((v, i) => `${x(lastHistIdx + 1 + i)},${y(v)}`),
  ].join(" ");

  return (
    <svg width={width} height={height} className={className} role="img" aria-label="forecast sparkline">
      {/* baseline */}
      <line
        x1={pad} x2={width - pad}
        y1={height - pad} y2={height - pad}
        stroke="currentColor" strokeOpacity="0.15"
      />
      {/* history (dashed) */}
      <polyline
        points={histPoints} fill="none" stroke="currentColor"
        strokeWidth="1.5" strokeDasharray="3 2" opacity="0.7"
      />
      {/* forecast (solid, accent colour) */}
      <polyline
        points={fcstPoints} fill="none"
        stroke="#4c9aff" strokeWidth="2"
      />
      {/* boundary divider */}
      <line
        x1={x(lastHistIdx)} x2={x(lastHistIdx)}
        y1={pad} y2={height - pad}
        stroke="currentColor" strokeOpacity="0.15" strokeDasharray="2 2"
      />
    </svg>
  );
}

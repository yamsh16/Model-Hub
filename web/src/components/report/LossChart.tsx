import { buildChart, CHART, type Line } from "@/lib/chart";
import { fmt } from "@/lib/format";
import type { TrainingLog } from "@/lib/types";
import { Panel } from "../ui";

const STROKE = { train: "var(--color-teal)", val: "var(--color-orange)" };
const LEGEND_FALLBACK = { train: "Training", val: "Validation" };

// Shown until the user loads their own log.
const SAMPLE = {
  train: "40,15 105,68 175,102 245,122 310,138 375,150 440,157 490,162",
  val: "40,20 105,75 175,110 245,132 310,146 375,150 440,149 490,147",
};

function Series({ line }: { line: Line }) {
  const common = { stroke: STROKE[line.role], strokeWidth: 3, strokeDasharray: line.role === "val" ? "8 6" : undefined };
  return (
    <>
      {line.runs.map((run, i) =>
        // A lone point has no line to draw, so show it as a dot.
        run.length === 1 ? (
          <circle key={i} cx={run[0].x} cy={run[0].y} r={3} fill={STROKE[line.role]} />
        ) : (
          <polyline key={i} fill="none" points={run.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")} {...common} />
        ),
      )}
    </>
  );
}

/** Training and validation loss over epochs/steps, drawn as inline SVG from the loaded log. */
export default function LossChart({ log }: { log: TrainingLog | null }) {
  const chart = log ? buildChart(log) : null;
  const { left, right, top, bottom } = CHART;
  const tick = "fill-muted font-mono text-[11px]";

  const label = chart
    ? `Loss over ${log!.x_name}: ${chart.lines.map((l) => l.name).join(" and ")}`
    : "Sample training and validation loss over epochs";
  const note = !log
    ? "Sample curve. Load your own training log on the connect page."
    : !chart
      ? `No loss column found. Columns: ${Object.keys(log.series).join(", ")}`
      : `${log.filename}: ${log.x.length} points. ${chart.lines.map((l) => `${l.name} ends at ${fmt(l.last)}`).join(", ")}.`;

  return (
    <Panel title="Loss curve">
      <svg viewBox={`0 0 ${CHART.width} ${CHART.height}`} role="img" aria-label={label} className="h-auto w-full">
        {chart ? (
          <>
            <path d={`M${left} ${top} V${bottom} H${right}`} fill="none" stroke="#8a94a3" strokeWidth={1.5} />
            <text x={4} y={top + 8} className={tick}>{fmt(chart.yMax)}</text>
            <text x={4} y={bottom} className={tick}>{fmt(chart.yMin)}</text>
            <text x={left} y={192} className={tick}>{fmt(chart.xMin)}</text>
            <text x={right} y={192} textAnchor="end" className={tick}>{fmt(chart.xMax)}</text>
            <text x={(left + right) / 2} y={198} textAnchor="middle" className="fill-muted text-sm">{log!.x_name}</text>
            {chart.lines.map((l) => (
              <Series key={l.role} line={l} />
            ))}
          </>
        ) : (
          <>
            <path d="M40 10 V175 H490" fill="none" stroke="#8a94a3" strokeWidth={1.5} />
            <polyline fill="none" stroke={STROKE.train} strokeWidth={3} points={SAMPLE.train} />
            <polyline fill="none" stroke={STROKE.val} strokeWidth={3} strokeDasharray="8 6" points={SAMPLE.val} />
            <text x={45} y={198} className="fill-muted text-sm">epochs</text>
          </>
        )}
      </svg>
      <div className="mt-2 flex gap-5 text-sm text-muted">
        {(["train", "val"] as const).map((role) => {
          const found = chart?.lines.find((l) => l.role === role);
          return (
            <span key={role} className="flex items-center gap-1.5">
              <i className="inline-block h-[3px] w-[18px]" style={{ background: STROKE[role] }} />
              {chart ? (found ? found.name : `${LEGEND_FALLBACK[role]} (not in log)`) : LEGEND_FALLBACK[role]}
            </span>
          );
        })}
      </div>
      <p className="mt-2 text-sm text-muted">{note}</p>
    </Panel>
  );
}

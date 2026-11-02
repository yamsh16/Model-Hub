// Geometry for the loss chart: turns a training log into SVG coordinates. No React in here,
// so it can be tested on its own.
import type { TrainingLog } from "./types";

export const CHART = { width: 500, height: 200, left: 52, right: 490, top: 10, bottom: 170 };

export type Point = { x: number; y: number };
export type Line = {
  name: string;
  role: "train" | "val";
  /** Runs of consecutive points; a missing value (null) breaks the line. */
  runs: Point[][];
  last: number;
};
export type ChartData = {
  lines: Line[];
  yMin: number;
  yMax: number;
  xMin: number;
  xMax: number;
};

/** Split a series into runs of consecutive numbers so gaps (null) break the line. */
export function toRuns(xs: number[], ys: (number | null)[], px: (x: number) => number, py: (y: number) => number): Point[][] {
  const runs: Point[][] = [];
  let current: Point[] = [];
  ys.forEach((y, i) => {
    if (y === null) {
      if (current.length) runs.push(current);
      current = [];
    } else {
      current.push({ x: px(xs[i]), y: py(y) });
    }
  });
  if (current.length) runs.push(current);
  return runs;
}

/** Which series to draw, and where. Returns null when the log has no loss column. */
export function buildChart(log: TrainingLog): ChartData | null {
  const wanted = [
    { name: log.train_loss, role: "train" as const },
    { name: log.val_loss, role: "val" as const },
  ].filter((l): l is { name: string; role: "train" | "val" } => !!l.name);
  if (wanted.length === 0) return null;

  const { left, right, top, bottom } = CHART;
  const values = wanted.flatMap((l) => log.series[l.name]).filter((v): v is number => v !== null);
  let yMin = Math.min(...values);
  let yMax = Math.max(...values);
  if (yMin === yMax) {
    yMin -= 0.5;
    yMax += 0.5;
  }
  const xMin = Math.min(...log.x);
  const xMax = Math.max(...log.x);
  const px = (x: number) => (xMax === xMin ? (left + right) / 2 : left + ((x - xMin) / (xMax - xMin)) * (right - left));
  const py = (y: number) => bottom - ((y - yMin) / (yMax - yMin)) * (bottom - top);

  const lines = wanted.map((l) => {
    const ys = log.series[l.name];
    return {
      name: l.name,
      role: l.role,
      runs: toRuns(log.x, ys, px, py),
      last: [...ys].reverse().find((v): v is number => v !== null) as number,
    };
  });
  return { lines, yMin, yMax, xMin, xMax };
}

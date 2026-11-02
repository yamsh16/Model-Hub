import { describe, expect, it } from "vitest";
import { buildChart, CHART, toRuns } from "./chart";
import type { TrainingLog } from "./types";

const log = (overrides: Partial<TrainingLog> = {}): TrainingLog => ({
  id: "l1",
  filename: "log.csv",
  source: "csv",
  x_name: "epoch",
  x: [1, 2, 3],
  series: { loss: [3, 2, 1], val_loss: [3.5, 2.5, 2] },
  train_loss: "loss",
  val_loss: "val_loss",
  ...overrides,
});

describe("toRuns", () => {
  it("breaks the line at missing values", () => {
    const runs = toRuns([1, 2, 3, 4], [1, null, 3, 4], (x) => x, (y) => y);
    expect(runs.map((r) => r.length)).toEqual([1, 2]);
  });
});

describe("buildChart", () => {
  it("is null when there is no loss column", () => {
    expect(buildChart(log({ train_loss: null, val_loss: null }))).toBeNull();
  });

  it("maps the lowest value to the bottom and the highest to the top of the plot", () => {
    const chart = buildChart(log())!;
    const ys = chart.lines.flatMap((l) => l.runs.flat().map((p) => p.y));
    expect(Math.max(...ys)).toBeCloseTo(CHART.bottom);
    expect(Math.min(...ys)).toBeCloseTo(CHART.top);
    expect(chart.yMin).toBe(1);
    expect(chart.yMax).toBe(3.5);
  });

  it("spreads the x axis over the plot width", () => {
    const xs = buildChart(log())!.lines[0].runs[0].map((p) => p.x);
    expect(xs[0]).toBeCloseTo(CHART.left);
    expect(xs[2]).toBeCloseTo(CHART.right);
  });

  it("draws just the series the log has", () => {
    const chart = buildChart(log({ val_loss: null }))!;
    expect(chart.lines.map((l) => l.role)).toEqual(["train"]);
  });

  it("handles a flat series and a single point without dividing by zero", () => {
    const chart = buildChart(log({ x: [5], series: { loss: [2], val_loss: [2] } }))!;
    const p = chart.lines[0].runs[0][0];
    expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
    expect(chart.yMax - chart.yMin).toBe(1);
  });

  it("reports each line's last value, skipping trailing gaps", () => {
    const chart = buildChart(log({ series: { loss: [3, 2, null], val_loss: [3, 2, 1] } }))!;
    expect(chart.lines[0].last).toBe(2);
  });
});

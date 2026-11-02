import { describe, expect, it } from "vitest";
import { estimateCard, progressCard } from "./report";
import type { Estimate } from "./types";

const base: Estimate = {
  series: "val_loss",
  x_name: "epoch",
  model: "power",
  n_points: 10,
  last_x: 10,
  r2: 0.98,
  status: "estimate",
  remaining: 5,
  remaining_low: 3,
  remaining_high: 9,
  high_unbounded: false,
  assumptions: [],
  criterion: "gain below 1%",
  warnings: [],
};

describe("estimateCard", () => {
  it("shows a placeholder with no log", () => {
    expect(estimateCard(null).value).toBe("–");
  });

  it("shows the estimate with its range", () => {
    expect(estimateCard(base)).toEqual({ value: "~5 epochs", note: "estimate from val_loss · range 3 to 9" });
  });

  it("says when the range has no clear end", () => {
    expect(estimateCard({ ...base, high_unbounded: true, remaining_high: null }).note).toContain("no clear end");
  });

  it("calls an unreliable estimate rough", () => {
    expect(estimateCard({ ...base, status: "unreliable" }).note).toMatch(/^rough estimate/);
  });

  it("explains the non-estimate outcomes", () => {
    expect(estimateCard({ ...base, status: "already_flat", remaining: null }).value).toBe("None");
    expect(estimateCard({ ...base, status: "rising", remaining: null }).value).toBe("Stop");
    expect(estimateCard({ ...base, status: "not_reached", remaining: null }).value).toBe("Many");
    expect(estimateCard({ ...base, status: "unreliable", remaining: null }).value).toBe("Unclear");
  });
});

describe("progressCard", () => {
  it("is the share of the way to the flattening point", () => {
    const p = progressCard(base); // 10 of 15
    expect(p.value).toBe("67%");
    expect(p.note).toBe("epoch 10 of about 15");
  });

  it("is complete when the curve has already levelled off", () => {
    expect(progressCard({ ...base, status: "already_flat", remaining: null }).fraction).toBe(1);
  });

  it("is unknown when it can't be judged", () => {
    expect(progressCard(null).fraction).toBeNull();
    expect(progressCard({ ...base, status: "rising", remaining: null }).note).toBe("Can't tell from this log");
  });
});

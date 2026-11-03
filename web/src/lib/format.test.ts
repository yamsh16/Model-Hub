import { describe, expect, it } from "vitest";
import { errorMessage, fmt, formatBytes, formatCount, pct } from "./format";

describe("format", () => {
  it("formats percentages", () => {
    expect(pct(0.8312)).toBe("83.1%");
    expect(pct(1)).toBe("100.0%");
  });

  it("keeps whole and large numbers whole, others to 3 significant digits", () => {
    expect(fmt(12)).toBe("12");
    expect(fmt(1234.56)).toBe("1235");
    expect(fmt(0.123456)).toBe("0.123");
  });

  it("turns a network failure into a readable message", () => {
    expect(errorMessage(new TypeError("Failed to fetch"))).toMatch(/Could not reach/);
    expect(errorMessage(new Error("Bad column"))).toBe("Bad column");
    expect(errorMessage("odd")).toBe("Something went wrong.");
  });
});

describe("sizes and counts", () => {
  it("formats file sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(formatBytes(150 * 1024 ** 2)).toBe("150 MB");
  });

  it("formats parameter counts without losing zeros", () => {
    expect(formatCount(45)).toBe("45");
    expect(formatCount(1500)).toBe("1.5K");
    expect(formatCount(12_000)).toBe("12K");
    expect(formatCount(100_000_000)).toBe("100M");
    expect(formatCount(1_000_000)).toBe("1M");
    expect(formatCount(1_230_000_000)).toBe("1.23B");
    expect(formatCount(7_000_000_000)).toBe("7B");
    expect(formatCount(70_600_000_000)).toBe("70.6B");
  });
});

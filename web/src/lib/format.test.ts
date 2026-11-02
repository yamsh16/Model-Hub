import { describe, expect, it } from "vitest";
import { errorMessage, fmt, pct } from "./format";

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

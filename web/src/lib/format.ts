/** 0.8312 -> "83.1%" */
export const pct = (x: number): string => `${(x * 100).toFixed(1)}%`;

/** Short number for axes and notes: whole numbers stay whole, others get 3 significant digits. */
export const fmt = (n: number): string =>
  Math.abs(n) >= 100 || Number.isInteger(n) ? String(Math.round(n)) : n.toPrecision(3);

/** The message to show for a failed request; a bare TypeError from fetch() means no connection. */
export function errorMessage(err: unknown): string {
  if (err instanceof TypeError) return "Could not reach the Model Hub API. Is the backend running?";
  return err instanceof Error ? err.message : "Something went wrong.";
}

/** 1536 -> "1.5 KB"; file sizes for people. */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return i === 0 ? `${bytes} B` : `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
}

/** 1_230_000_000 -> "1.23B"; parameter counts. */
export function formatCount(n: number): string {
  for (const [size, suffix] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]] as const) {
    if (n >= size) {
      const digits = (n / size).toPrecision(3);
      // strip trailing zeros only after a decimal point ("1.50" -> "1.5", but "100" stays "100")
      return `${digits.includes(".") ? digits.replace(/0+$/, "").replace(/\.$/, "") : digits}${suffix}`;
    }
  }
  return String(n);
}

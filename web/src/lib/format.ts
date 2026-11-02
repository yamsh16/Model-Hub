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

// Turns the backend's estimate into the words and numbers on the report cards.
import { fmt } from "./format";
import type { Estimate } from "./types";

export type Card = { value: string; note: string };

/** "Training still needed" card. */
export function estimateCard(est: Estimate | null): Card {
  if (!est) return { value: "–", note: "Load a training log to estimate this" };

  const unit = `${est.x_name}s`;
  const range =
    est.remaining_low === null
      ? ""
      : est.high_unbounded
        ? ` · range ${fmt(est.remaining_low)} to no clear end`
        : ` · range ${fmt(est.remaining_low)} to ${fmt(est.remaining_high ?? est.remaining_low)}`;
  const label = est.status === "unreliable" ? "rough estimate" : "estimate";

  if (est.remaining !== null && (est.status === "estimate" || est.status === "unreliable")) {
    return { value: `~${fmt(est.remaining)} ${unit}`, note: `${label} from ${est.series}${range}` };
  }
  switch (est.status) {
    case "already_flat":
      return { value: "None", note: `${est.series} has already levelled off` };
    case "rising":
      return { value: "Stop", note: `${est.series} is rising: likely overfitting` };
    case "not_reached":
      return { value: "Many", note: `${est.series} is still falling steadily` };
    default:
      return { value: "Unclear", note: "Log too short to estimate" };
  }
}

/** Training progress = how far along the curve is before gains flatten. `fraction` is 0..1, or null. */
export function progressCard(est: Estimate | null): Card & { fraction: number | null } {
  let fraction: number | null = null;
  if (est?.status === "already_flat") fraction = 1;
  else if (est?.status === "estimate" && est.remaining !== null && est.last_x + est.remaining > 0) {
    fraction = est.last_x / (est.last_x + est.remaining);
  }
  if (fraction === null || !est) {
    return { fraction: null, value: "–", note: est ? "Can't tell from this log" : "Load a training log" };
  }
  const note =
    fraction === 1
      ? `${est.series} has levelled off`
      : `${est.x_name} ${fmt(est.last_x)} of about ${fmt(est.last_x + (est.remaining ?? 0))}`;
  return { fraction, value: `${Math.round(fraction * 100)}%`, note };
}

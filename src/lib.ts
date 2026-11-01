import type { Evidence, Report } from "./types";

export const clamp01 = (n: number) => Math.max(0, Math.min(1, Number(n) || 0));
export const fixed = (n: number, d = 2) => (Number.isFinite(n) ? n.toFixed(d) : (0).toFixed(d));
export const pct = (n: number) => `${Math.round(clamp01(n) * 100)}%`;

export function shortDate(iso?: string | null) {
  if (!iso) return "undated";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  const m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${String(d.getUTCDate()).padStart(2, "0")} ${m[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function stamp(iso?: string) {
  const d = iso ? new Date(iso) : new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(
    d.getUTCMinutes()
  )}Z`;
}

export function strengthWord(value: number, label?: string) {
  if (label) return label;
  if (value >= 0.8) return "High";
  if (value >= 0.6) return "Moderate";
  if (value >= 0.4) return "Thin";
  return "Low";
}

export function stanceTone(stance: string) {
  if (stance === "SUPPORTS") return "support";
  if (stance === "REFUTES") return "refute";
  return "neutral";
}

export function guardState(ev: Evidence) {
  const a = ev.analysis;
  if (a.quote_verified !== true) return { state: "dropped" as const, label: "Withheld" };
  if (a.quote_mode === "insertions") {
    const n = a.quote_insertions || 1;
    return { state: "edited" as const, label: `${n} join${n > 1 ? "s" : ""}, marked` };
  }
  return { state: "exact" as const, label: "Quote verified" };
}

export function arithmeticFor(report: Report, claimIndex: number) {
  const c = report.claims[claimIndex];
  const s = c.scores;
  const total = Math.round((s.c.points + s.r.points + s.a.points + s.t.points + s.k.points) * 1000) / 1000;
  return {
    body: `${fixed(s.c.points, 3)} + ${fixed(s.r.points, 3)} + ${fixed(s.a.points, 3)} + ${fixed(
      s.t.points,
      3
    )} + ${fixed(s.k.points, 3)}`,
    total,
  };
}

export function wordCount(text: string) {
  const m = text.trim().match(/\S+/g);
  return m ? m.length : 0;
}

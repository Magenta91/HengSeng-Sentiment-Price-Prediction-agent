export const pct = (v: number | null | undefined, digits = 2) => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`);
export const num = (v: number | null | undefined, digits = 2) =>
  v == null ? "n/a" : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const score = (v: number | null | undefined) => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}`);
export const dateShort = (s: string | null | undefined) => (s ? s.slice(0, 10) : "n/a");

export const DIRECTION: Record<string, { glyph: string; label: string; cls: string }> = {
  up: { glyph: "▲", label: "Up", cls: "text-up" },
  down: { glyph: "▼", label: "Down", cls: "text-down" },
  mixed: { glyph: "◆", label: "Mixed", cls: "text-flat" },
  sideways: { glyph: "▬", label: "Sideways", cls: "text-flat" },
  unclear: { glyph: "?", label: "Unclear", cls: "text-flat" },
};
export const signClass = (v: number | null | undefined) => (v == null ? "text-flat" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-flat");

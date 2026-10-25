// Date, duration and size formatting in the Databricks style.

const dateFmt = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
});

const fullFmt = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
  timeZoneName: "short",
});

export function toMillis(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") return value > 0 ? value : null;
  const asNum = Number(value);
  if (!Number.isNaN(asNum)) return asNum > 0 ? asNum : null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** "Oct 25, 2026, 08:16 AM" */
export function formatDate(value: number | string | null | undefined): string {
  const ms = toMillis(value);
  return ms === null ? "–" : dateFmt.format(ms);
}

/** Full timestamp for hover titles. */
export function formatFull(value: number | string | null | undefined): string {
  const ms = toMillis(value);
  return ms === null ? "" : fullFmt.format(ms);
}

/** "just now", "3 min ago", "2 hours ago", "4 days ago", then the date. */
export function formatRelative(value: number | string | null | undefined, now = Date.now()): string {
  const ms = toMillis(value);
  if (ms === null) return "–";
  const diff = now - ms;
  const future = diff < 0;
  const abs = Math.abs(diff);
  const sec = Math.round(abs / 1000);
  let text: string;
  if (sec < 10) return "just now";
  if (sec < 60) text = `${sec} sec`;
  else if (sec < 3600) text = `${Math.round(sec / 60)} min`;
  else if (sec < 86400) {
    const h = Math.round(sec / 3600);
    text = `${h} hour${h === 1 ? "" : "s"}`;
  } else if (sec < 86400 * 30) {
    const d = Math.round(sec / 86400);
    text = `${d} day${d === 1 ? "" : "s"}`;
  } else return formatDate(ms);
  return future ? `in ${text}` : `${text} ago`;
}

/** "450ms", "12s", "3m 12s", "1h 02m" */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0 || Number.isNaN(ms)) return "–";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return n.toLocaleString("en-US");
}

/** Plain-English rendering of the common Quartz cron shapes; falls back to the expression. */
export function describeQuartz(expr: string): string {
  const parts = expr.trim().split(/\s+/);
  if (parts.length < 6) return expr;
  const [sec, min, hour, dom, month, dow] = parts;
  const isNum = (v: string) => /^\d+$/.test(v);
  const at = (h: string, m: string) => `${h.padStart(2, "0")}:${m.padStart(2, "0")}`;
  if (sec === "0" && isNum(min) && isNum(hour) && (dom === "*" || dom === "?") && month === "*") {
    if (dow === "*" || dow === "?") return `Every day at ${at(hour, min)}`;
    if (/^[A-Z,-]+$/i.test(dow) || /^[\d,-]+$/.test(dow)) return `At ${at(hour, min)} on ${dow}`;
  }
  if (sec === "0" && isNum(min) && hour === "*" && (dom === "*" || dom === "?") && month === "*") {
    return `Every hour at :${min.padStart(2, "0")}`;
  }
  const every = /^0\/(\d+)$|^\*\/(\d+)$/;
  const mm = min.match(every);
  if (sec === "0" && mm && hour === "*") return `Every ${mm[1] ?? mm[2]} minutes`;
  return expr;
}

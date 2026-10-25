import { useEffect, useState } from "react";
import { formatDate, formatFull, formatRelative, toMillis } from "../lib/format";

let tick = Date.now();
const subscribers = new Set<(n: number) => void>();
let timer: number | undefined;

function useNow(): number {
  const [now, setNow] = useState(tick);
  useEffect(() => {
    subscribers.add(setNow);
    if (timer === undefined) {
      timer = window.setInterval(() => {
        tick = Date.now();
        subscribers.forEach((s) => s(tick));
      }, 30_000);
    }
    return () => {
      subscribers.delete(setNow);
      if (subscribers.size === 0 && timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };
  }, []);
  return now;
}

/** "3 min ago" with the full timestamp on hover. */
export function RelTime({ value }: { value: number | string | null | undefined }) {
  const now = useNow();
  if (toMillis(value) === null) return <span className="muted">–</span>;
  return (
    <span title={formatFull(value)} className="nowrap">
      {formatRelative(value, Math.max(now, Date.now() - 1))}
    </span>
  );
}

/** "Oct 25, 2026, 08:16 AM" with the full timestamp on hover. */
export function DateTime({ value }: { value: number | string | null | undefined }) {
  if (toMillis(value) === null) return <span className="muted">–</span>;
  return (
    <span title={formatFull(value)} className="nowrap">
      {formatDate(value)}
    </span>
  );
}

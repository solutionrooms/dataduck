import type { ReactNode } from "react";
import { CircleX, Construction, Inbox, LoaderCircle, TriangleAlert } from "lucide-react";
import type { UseQueryResult } from "@tanstack/react-query";
import { errorMessage, isUnavailable } from "../api/client";

export function Spinner({ size = 16 }: { size?: number }) {
  return <LoaderCircle size={size} className="spin" />;
}

export function Loading({ label = "Loading…", compact }: { label?: string; compact?: boolean }) {
  return (
    <div className={`state${compact ? " compact" : ""}`}>
      <span className="loading-inline">
        <Spinner /> {label}
      </span>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  compact,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={`state${compact ? " compact" : ""}`}>
      {!compact && <span className="state-icon">{icon ?? <Inbox size={22} />}</span>}
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="banner error" role="alert">
      <CircleX size={16} />
      <div className="banner-body">{errorMessage(error)}</div>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function WarningBanner({ children }: { children: ReactNode }) {
  return (
    <div className="banner warning">
      <TriangleAlert size={16} />
      <div className="banner-body">{children}</div>
    </div>
  );
}

export function Unavailable({ what, compact }: { what: string; compact?: boolean }) {
  return (
    <EmptyState
      compact={compact}
      icon={<Construction size={22} />}
      title={`${what} isn't available yet`}
      description="This Duckbricks build does not serve this endpoint yet. It will appear here once the backend supports it."
    />
  );
}

/**
 * Render the non-data states of a query: loading, unavailable endpoint (404 ENDPOINT_NOT_FOUND)
 * or an error banner. Returns null once data is present.
 */
export function QueryState({
  query,
  what,
  compact,
  loadingLabel,
}: {
  query: Pick<UseQueryResult<unknown>, "isPending" | "isError" | "error" | "refetch" | "data">;
  what: string;
  compact?: boolean;
  loadingLabel?: string;
}) {
  if (query.isPending) return <Loading compact={compact} label={loadingLabel} />;
  if (query.isError) {
    if (isUnavailable(query.error)) return <Unavailable what={what} compact={compact} />;
    return <ErrorBanner error={query.error} onRetry={() => query.refetch()} />;
  }
  return null;
}

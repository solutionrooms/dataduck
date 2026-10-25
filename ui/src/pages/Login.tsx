import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";
import { KeyRound } from "lucide-react";
import { ApiError, api, errorMessage, getToken, setToken } from "../api/client";
import type { PlatformStatus } from "../api/types";
import { DuckMark } from "../components/Icons";
import { Spinner } from "../components/States";

export function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [token, setTokenValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const next = params.get("next") || "/";
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";

  if (getToken() && !busy) return <Navigate to={safeNext} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = token.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      await api<PlatformStatus>("/api/duckbricks/status", { token: value });
      setToken(value);
      navigate(safeNext, { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401 ? "That token was not accepted." : errorMessage(err),
      );
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <DuckMark size={40} />
        <h1>Sign in to Duckbricks</h1>
        <p className="secondary" style={{ marginTop: 0, marginBottom: 20 }}>
          Paste the API token from <span className="mono">DBK_TOKEN</span> in your <span className="mono">.env</span>.
        </p>
        <div className="field">
          <label htmlFor="token">Personal access token</label>
          <div className="search-input">
            <KeyRound size={14} />
            <input
              id="token"
              className="input"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={token}
              onChange={(e) => setTokenValue(e.target.value)}
              placeholder="Paste your token"
            />
          </div>
        </div>
        {error && (
          <div className="banner error" role="alert">
            <div className="banner-body">{error}</div>
          </div>
        )}
        <button className="btn btn-primary" style={{ width: "100%", height: 36 }} disabled={busy || !token.trim()}>
          {busy && <Spinner size={14} />}
          Sign in
        </button>
      </form>
    </div>
  );
}

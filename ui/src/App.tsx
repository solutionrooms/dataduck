import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { getToken, onUnauthorized } from "./api/client";
import { Layout } from "./components/Layout";
import { toast } from "./lib/toast";
import { LoginPage } from "./pages/Login";
import { HomePage } from "./pages/Home";
import { WorkspacePage } from "./pages/Workspace";
import { FileEditorPage } from "./pages/FileEditor";
import { CatalogPage } from "./pages/Catalog";
import { JobsPage } from "./pages/Jobs";
import { JobDetailPage } from "./pages/JobDetail";
import { RunDetailPage } from "./pages/RunDetail";
import { RunsPage } from "./pages/Runs";
import { SqlEditorPage } from "./pages/SqlEditor";
import { QueryHistoryPage } from "./pages/QueryHistory";
import { WarehousesPage } from "./pages/Warehouses";
import { ComputePage } from "./pages/Compute";
import { PipelinePage } from "./pages/Pipeline";
import { RecentsPage } from "./pages/Recents";
import { NotFoundPage } from "./pages/NotFound";

function RequireAuth() {
  const location = useLocation();
  if (!getToken()) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login${next && next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`} replace />;
  }
  return <Layout />;
}

export function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();

  useEffect(
    () =>
      onUnauthorized(() => {
        qc.clear();
        if (window.location.pathname !== "/login") {
          toast.error("Your token was rejected. Sign in again.");
          const next = window.location.pathname + window.location.search;
          navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true });
        }
      }),
    [navigate, qc],
  );

  useEffect(() => {
    document.title = "Duckbricks";
  }, [location.pathname]);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<HomePage />} />
        <Route path="browse/*" element={<WorkspacePage />} />
        <Route path="editor/*" element={<FileEditorPage />} />
        <Route path="explore/data/*" element={<CatalogPage />} />
        <Route path="explore" element={<Navigate to="/explore/data" replace />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/:jobId" element={<JobDetailPage />} />
        <Route path="jobs/:jobId/runs/:runId" element={<RunDetailPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="sql" element={<Navigate to="/sql/editor" replace />} />
        <Route path="sql/editor" element={<SqlEditorPage />} />
        <Route path="sql/history" element={<QueryHistoryPage />} />
        <Route path="sql/warehouses" element={<WarehousesPage />} />
        <Route path="compute" element={<ComputePage />} />
        <Route path="pipelines/:pipelineId" element={<PipelinePage />} />
        <Route path="recents" element={<RecentsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

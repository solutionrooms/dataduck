// Workspace file editor: export -> CodeMirror -> import with overwrite.

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, Save, SquareTerminal } from "lucide-react";
import { base64ToText, get, post, textToBase64 } from "../api/client";
import { keys, useStatus } from "../api/hooks";
import type { WorkspaceObject } from "../api/types";
import { Editor } from "../components/Editor";
import { ObjectIcon } from "../components/Icons";
import { QueryState, Spinner } from "../components/States";
import { RelTime } from "../components/Time";
import { addRecent } from "../lib/recents";
import { basename, browseUrl, dirname, editorUrl, languageForPath, notebookUrl, pathFromSplat } from "../lib/paths";
import { openSqlTab } from "../lib/sqlTabs";
import { toast } from "../lib/toast";

export function FileEditorPage() {
  const params = useParams();
  const path = pathFromSplat(params["*"]);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: status } = useStatus();

  const meta = useQuery({
    queryKey: ["ws-status", path],
    queryFn: () => get<WorkspaceObject>("/api/2.0/workspace/get-status", { path }),
  });
  const file = useQuery({
    queryKey: ["ws-export", path],
    queryFn: () => get<{ content?: string }>("/api/2.0/workspace/export", { path }).then((r) => base64ToText(r.content ?? "")),
    staleTime: Infinity,
    gcTime: 0,
  });

  const [text, setText] = useState<string | null>(null);
  const [savedText, setSavedText] = useState<string | null>(null);
  useEffect(() => {
    setText(null);
    setSavedText(null);
  }, [path]);
  useEffect(() => {
    if (file.data !== undefined) {
      setText(file.data);
      setSavedText(file.data);
    }
  }, [file.data]);

  const isNotebook = meta.data?.object_type === "NOTEBOOK";
  useEffect(() => {
    if (!meta.data) return;
    addRecent({
      kind: isNotebook ? "notebook" : "file",
      title: basename(path),
      url: editorUrl(path),
      href: isNotebook ? notebookUrl(status?.links.notebooks, path) : undefined,
      subtitle: dirname(path),
    });
  }, [meta.data, isNotebook, path, status?.links.notebooks]);

  const dirty = text !== null && savedText !== null && text !== savedText;

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  const save = useMutation({
    mutationFn: (content: string) =>
      post("/api/2.0/workspace/import", { path, content: textToBase64(content), format: "AUTO", overwrite: true }).then(
        () => content,
      ),
    onSuccess: (content) => {
      setSavedText(content);
      qc.invalidateQueries({ queryKey: ["ws-status", path] });
      qc.invalidateQueries({ queryKey: keys.wsList(dirname(path)) });
      toast.success(`Saved ${basename(path)}`);
    },
  });

  const doSave = () => {
    if (text !== null && !save.isPending) save.mutate(text);
  };

  const parent = dirname(path);
  const segments = parent.split("/").filter(Boolean);
  const language = languageForPath(path);

  return (
    <div className="panel">
      <div className="page-fill">
        <div className="page" style={{ paddingBottom: 12, flexShrink: 0 }}>
          <div className="breadcrumb">
            <Link to="/browse?root=1">Workspace</Link>
            {segments.map((seg, i) => (
              <span key={i} className="row" style={{ gap: 4 }}>
                <ChevronRight size={14} />
                <Link to={browseUrl(`/${segments.slice(0, i + 1).join("/")}`)}>{seg}</Link>
              </span>
            ))}
            <ChevronRight size={14} />
          </div>
          <div className="page-header" style={{ marginBottom: 0 }}>
            <h1 className="page-title">
              <ObjectIcon type={meta.data?.object_type ?? "FILE"} path={path} size={20} />
              <span className="title-text">{basename(path)}</span>
            </h1>
            <span className="secondary nowrap" style={{ fontSize: 12 }}>
              {save.isPending ? (
                <span className="loading-inline">
                  <Spinner size={12} /> Saving…
                </span>
              ) : dirty ? (
                "Unsaved changes"
              ) : meta.data?.modified_at ? (
                <>
                  Saved <RelTime value={meta.data.modified_at} />
                </>
              ) : null}
            </span>
            <div className="page-actions">
              {isNotebook && (
                <a className="btn" href={notebookUrl(status?.links.notebooks, path)} target="_blank" rel="noreferrer">
                  <ExternalLink size={14} />
                  Open in marimo
                </a>
              )}
              {language === "sql" && (
                <button
                  className="btn"
                  onClick={() => {
                    openSqlTab(text ?? "", basename(path));
                    navigate("/sql/editor");
                  }}
                >
                  <SquareTerminal size={14} />
                  Open in SQL editor
                </button>
              )}
              <button className="btn btn-primary" onClick={doSave} disabled={!dirty || save.isPending} title="Save (⌘S)">
                <Save size={14} />
                Save
              </button>
            </div>
          </div>
        </div>
        <div style={{ flex: 1, minHeight: 0, borderTop: "1px solid var(--border)" }}>
          {text !== null ? (
            <Editor value={text} onChange={setText} language={language} onSave={doSave} />
          ) : (
            <div className="page">
              <QueryState query={file} what="File export" loadingLabel="Opening file…" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

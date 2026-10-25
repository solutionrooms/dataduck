// SQL editor: tabs, schema browser, catalog/schema/warehouse pickers, results grid.

import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { Link } from "react-router";
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Columns3,
  Database,
  Download,
  Play,
  Plus,
  ScrollText,
  Search,
  Square,
  X,
} from "lucide-react";
import { errorMessage, post } from "../api/client";
import {
  executeStatement,
  useCatalogs,
  useSchemas,
  useTable,
  useTables,
  useWarehouses,
  type StatementResult,
} from "../api/hooks";
import { useTableIndex } from "../api/search";
import { DataGrid, downloadText, toCsv } from "../components/DataGrid";
import { Editor, type CodeEditorHandle } from "../components/Editor";
import { CatalogIcon, TableIcon } from "../components/Icons";
import { Spinner } from "../components/States";
import { formatDuration, formatNumber } from "../lib/format";
import { activateSqlTab, closeSqlTab, openSqlTab, updateSqlTab, useSqlTabs, type SqlTab } from "../lib/sqlTabs";

interface RunState {
  running: boolean;
  startedAt?: number;
  statementId?: string;
  canceled?: boolean;
  result?: StatementResult;
  error?: string;
}

const ROW_LIMIT = 10_000;

export function SqlEditorPage() {
  const { tabs, activeId } = useSqlTabs();
  const tab = tabs.find((t) => t.id === activeId) ?? tabs[0];
  const editorRef = useRef<CodeEditorHandle | null>(null);
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const runsRef = useRef(runs);
  runsRef.current = runs;
  const [editorHeight, setEditorHeight] = useState(300);
  const index = useTableIndex(true);

  const sqlSchema = useMemo(() => {
    const ns: Record<string, Record<string, Record<string, string[]>>> = {};
    for (const t of index.data ?? []) {
      const [c, s, n] = t.full_name.split(".");
      ((ns[c] ??= {})[s] ??= {})[n] = [];
    }
    return ns;
  }, [index.data]);

  const patchRun = (id: string, patch: Partial<RunState>) =>
    setRuns((r) => ({ ...r, [id]: { ...(r[id] ?? { running: false }), ...patch } }));

  const run = async () => {
    if (!tab) return;
    const statement = (editorRef.current?.getSelectionOrAll() ?? tab.sql).trim();
    if (!statement || runsRef.current[tab.id]?.running) return;
    const tabId = tab.id;
    patchRun(tabId, { running: true, startedAt: Date.now(), statementId: undefined, canceled: false, result: undefined, error: undefined });
    try {
      const result = await executeStatement(
        {
          statement,
          catalog: tab.catalog || undefined,
          schema: tab.catalog && tab.schema ? tab.schema : undefined,
          warehouse_id: tab.warehouseId || undefined,
          row_limit: ROW_LIMIT,
        },
        { onStarted: (statementId) => patchRun(tabId, { statementId }) },
      );
      if (runsRef.current[tabId]?.canceled) return;
      patchRun(tabId, { running: false, result });
    } catch (err) {
      if (runsRef.current[tabId]?.canceled) return;
      patchRun(tabId, { running: false, error: errorMessage(err) });
    }
  };

  const cancel = async () => {
    const state = runs[tab.id];
    patchRun(tab.id, { running: false, canceled: true });
    if (state?.statementId) {
      try {
        await post(`/api/2.0/sql/statements/${state.statementId}/cancel`, {});
      } catch {
        // already finished
      }
    }
  };

  const startResize = (e: ReactMouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = editorHeight;
    const target = e.currentTarget as HTMLElement;
    target.classList.add("dragging");
    const onMove = (ev: MouseEvent) => setEditorHeight(Math.max(120, Math.min(window.innerHeight - 260, startH + ev.clientY - startY)));
    const onUp = () => {
      target.classList.remove("dragging");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const state = runs[tab.id];

  return (
    <div className="panel split">
      <SchemaBrowser onInsert={(text) => editorRef.current?.insertText(text)} />
      <div className="split-main page-fill">
        <EditorTabs tabs={tabs} activeId={tab.id} runs={runs} />
        <Toolbar tab={tab} running={!!state?.running} onRun={run} onCancel={cancel} />
        <div style={{ height: editorHeight, flexShrink: 0 }}>
          <Editor
            key={tab.id}
            ref={editorRef}
            value={tab.sql}
            onChange={(sql) => updateSqlTab(tab.id, { sql })}
            language="sql"
            onRun={run}
            sqlSchema={sqlSchema}
            placeholder="Write a query. ⌘/Ctrl + Enter runs the selection, or everything."
          />
        </div>
        <div className="resize-handle" onMouseDown={startResize} />
        <Results state={state} onCancel={cancel} tabTitle={tab.title} />
      </div>
    </div>
  );
}

// --- tabs ------------------------------------------------------------------------------------

function EditorTabs({ tabs, activeId, runs }: { tabs: SqlTab[]; activeId: string; runs: Record<string, RunState> }) {
  const [renaming, setRenaming] = useState<string | null>(null);
  return (
    <div className="editor-tabs">
      {tabs.map((t) => (
        <div
          key={t.id}
          className={`editor-tab${t.id === activeId ? " active" : ""}`}
          onClick={() => activateSqlTab(t.id)}
          onDoubleClick={() => setRenaming(t.id)}
          title="Double-click to rename"
        >
          {runs[t.id]?.running ? <Spinner size={12} /> : <ScrollText size={13} />}
          {renaming === t.id ? (
            <input
              autoFocus
              defaultValue={t.title}
              onClick={(e) => e.stopPropagation()}
              onBlur={(e) => {
                updateSqlTab(t.id, { title: e.target.value.trim() || t.title });
                setRenaming(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                if (e.key === "Escape") setRenaming(null);
              }}
            />
          ) : (
            <span className="tab-title">{t.title}</span>
          )}
          <button
            className="icon-btn sm"
            aria-label="Close tab"
            onClick={(e) => {
              e.stopPropagation();
              closeSqlTab(t.id);
            }}
          >
            <X size={12} />
          </button>
        </div>
      ))}
      <button className="icon-btn" title="New query" onClick={() => openSqlTab()} style={{ marginBottom: 2 }}>
        <Plus size={16} />
      </button>
    </div>
  );
}

// --- toolbar ---------------------------------------------------------------------------------

function Toolbar({
  tab,
  running,
  onRun,
  onCancel,
}: {
  tab: SqlTab;
  running: boolean;
  onRun: () => void;
  onCancel: () => void;
}) {
  const catalogs = useCatalogs();
  const schemas = useSchemas(tab.catalog || null);
  const warehouses = useWarehouses();
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

  return (
    <div className="row" style={{ padding: "8px 12px", gap: 8, flexShrink: 0, flexWrap: "wrap" }}>
      {running ? (
        <button className="btn" onClick={onCancel}>
          <Square size={12} fill="currentColor" />
          Cancel
        </button>
      ) : (
        <button className="btn btn-primary" onClick={onRun} title={`Run (${isMac ? "⌘" : "Ctrl"} + Enter)`}>
          <Play size={13} fill="currentColor" />
          Run
          <span style={{ opacity: 0.75, fontSize: 11, fontWeight: 400 }}>{isMac ? "⌘↵" : "Ctrl+↵"}</span>
        </button>
      )}
      <select
        className="select"
        value={tab.catalog ?? ""}
        onChange={(e) => updateSqlTab(tab.id, { catalog: e.target.value || undefined, schema: undefined })}
        aria-label="Catalog"
        style={{ minWidth: 140 }}
      >
        <option value="">Catalog</option>
        {(catalogs.data ?? []).map((c) => (
          <option key={c.name} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>
      <select
        className="select"
        value={tab.schema ?? ""}
        onChange={(e) => updateSqlTab(tab.id, { schema: e.target.value || undefined })}
        aria-label="Schema"
        disabled={!tab.catalog}
        style={{ minWidth: 140 }}
      >
        <option value="">Schema</option>
        {(schemas.data ?? []).map((s) => (
          <option key={s.name} value={s.name}>
            {s.name}
          </option>
        ))}
      </select>
      <span className="spacer" />
      <select
        className="select"
        value={tab.warehouseId ?? ""}
        onChange={(e) => updateSqlTab(tab.id, { warehouseId: e.target.value || undefined })}
        aria-label="Warehouse"
        style={{ minWidth: 220 }}
      >
        <option value="">Serverless (in-process)</option>
        {(warehouses.data ?? []).map((w) => (
          <option key={w.id} value={w.id}>
            {w.name} · {w.state.toLowerCase()}
          </option>
        ))}
      </select>
    </div>
  );
}

// --- results ---------------------------------------------------------------------------------

function Elapsed({ since }: { since: number }) {
  const [, force] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => force((n) => n + 1), 200);
    return () => window.clearInterval(t);
  }, []);
  return <>{formatDuration(Date.now() - since)}</>;
}

function Results({ state, onCancel, tabTitle }: { state?: RunState; onCancel: () => void; tabTitle: string }) {
  const body = () => {
    if (!state || (!state.running && !state.result && !state.error && !state.canceled)) {
      return (
        <div className="state">
          <p className="muted">
            Run a query to see results here. <span className="kbd">⌘ Enter</span> runs the selection, or everything.
          </p>
        </div>
      );
    }
    if (state.running) {
      return (
        <div className="state">
          <span className="loading-inline">
            <Spinner /> Running… {state.startedAt && <Elapsed since={state.startedAt} />}
          </span>
          <button className="btn btn-sm" onClick={onCancel}>
            Cancel
          </button>
        </div>
      );
    }
    if (state.canceled) {
      return (
        <div className="state">
          <p>Query canceled.</p>
        </div>
      );
    }
    if (state.error) {
      return (
        <div style={{ padding: 12 }}>
          <div className="error-block">{state.error}</div>
        </div>
      );
    }
    const { response, rows, durationMs } = state.result!;
    if (response.status.state !== "SUCCEEDED") {
      return (
        <div style={{ padding: 12 }}>
          <div className="row" style={{ marginBottom: 8, color: "var(--danger)", fontWeight: 600 }}>
            Query {response.status.state.toLowerCase()} · {formatDuration(durationMs)}
          </div>
          <div className="error-block">{response.status.error?.message ?? "The statement did not succeed."}</div>
        </div>
      );
    }
    const columns = response.manifest?.schema.columns ?? [];
    const total = response.manifest?.total_row_count ?? rows.length;
    return (
      <>
        <div className="grid-footer" style={{ borderTop: "none" }}>
          <span className="status status-success">
            <CircleCheck size={14} />
          </span>
          <span>
            {formatNumber(rows.length)} row{rows.length === 1 ? "" : "s"}
            {total > rows.length ? ` of ${formatNumber(total)}` : ""}
          </span>
          <span>·</span>
          <span>{formatDuration(durationMs)}</span>
          {response.manifest?.truncated && <span className="badge amber">Truncated at {formatNumber(ROW_LIMIT)} rows</span>}
          <span className="spacer" />
          {columns.length > 0 && (
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => downloadText(`${tabTitle.replace(/[^\w.-]+/g, "_") || "results"}.csv`, toCsv(columns, rows))}
            >
              <Download size={12} />
              Download CSV
            </button>
          )}
        </div>
        {columns.length ? (
          <div style={{ flex: 1, minHeight: 0 }}>
            <DataGrid columns={columns} rows={rows} />
          </div>
        ) : (
          <div className="state compact">
            <p>Statement executed. No rows returned.</p>
          </div>
        )}
      </>
    );
  };
  return <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>{body()}</div>;
}

// --- schema browser --------------------------------------------------------------------------

function SchemaBrowser({ onInsert }: { onInsert: (text: string) => void }) {
  const catalogs = useCatalogs();
  const [filter, setFilter] = useState("");
  const term = filter.trim().toLowerCase();
  const index = useTableIndex(true);
  return (
    <aside className="side-tree" style={{ width: 250 }} aria-label="Schema browser">
      <div className="side-tree-title" style={{ fontSize: 13 }}>
        <Database size={15} />
        Schema browser
        <span className="spacer" />
        <Link to="/sql/history" className="icon-btn sm" title="Query history">
          <ScrollText size={13} />
        </Link>
      </div>
      <div className="search-input" style={{ margin: "0 4px 10px" }}>
        <Search size={14} />
        <input className="input" placeholder="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ height: 30 }} />
      </div>
      <div className="tree">
        {term ? (
          (index.data ?? [])
            .filter((t) => t.full_name.toLowerCase().includes(term))
            .slice(0, 200)
            .map((t) => <TableNode key={t.full_name} fullName={t.full_name} tableType={t.table_type} depth={0} onInsert={onInsert} showSchema />)
        ) : catalogs.data ? (
          catalogs.data.map((c) => <BrowserCatalog key={c.name} name={c.name} onInsert={onInsert} />)
        ) : (
          <div className="tree-note" style={{ paddingLeft: 8 }}>
            {catalogs.isError ? "Failed to load catalogs" : "Loading…"}
          </div>
        )}
      </div>
    </aside>
  );
}

function BrowserRow({
  depth,
  open,
  toggle,
  icon,
  label,
  meta,
  onClick,
  title,
}: {
  depth: number;
  open?: boolean;
  toggle?: () => void;
  icon: ReactNode;
  label: string;
  meta?: string;
  onClick: () => void;
  title?: string;
}) {
  return (
    <div className="tree-row" style={{ paddingLeft: 4 + depth * 14 }} onClick={onClick} title={title ?? `Click to insert ${label}`}>
      {toggle ? (
        <span
          className="twisty"
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      ) : (
        <span style={{ width: 18, flexShrink: 0 }} />
      )}
      {icon}
      <span className="label">{label}</span>
      {meta && <span className="meta mono">{meta}</span>}
    </div>
  );
}

function BrowserCatalog({ name, onInsert }: { name: string; onInsert: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  const schemas = useSchemas(name, open);
  return (
    <>
      <BrowserRow depth={0} open={open} toggle={() => setOpen(!open)} icon={<CatalogIcon size={15} />} label={name} onClick={() => setOpen(!open)} title={name} />
      {open &&
        (schemas.data ?? []).map((s) => <BrowserSchema key={s.name} catalog={name} schema={s.name} onInsert={onInsert} />)}
      {open && !schemas.data && <div className="tree-note" style={{ paddingLeft: 40 }}>{schemas.isError ? "Failed to load" : "Loading…"}</div>}
    </>
  );
}

function BrowserSchema({ catalog, schema, onInsert }: { catalog: string; schema: string; onInsert: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  const tables = useTables(catalog, schema, open);
  return (
    <>
      <BrowserRow
        depth={1}
        open={open}
        toggle={() => setOpen(!open)}
        icon={<Database size={14} className="icon-folder" />}
        label={schema}
        onClick={() => setOpen(!open)}
        title={`${catalog}.${schema}`}
      />
      {open &&
        (tables.data ?? []).map((t) => (
          <TableNode key={t.name} fullName={t.full_name} tableType={t.table_type} depth={2} onInsert={onInsert} />
        ))}
      {open && tables.data?.length === 0 && <div className="tree-note" style={{ paddingLeft: 54 }}>No tables</div>}
      {open && !tables.data && <div className="tree-note" style={{ paddingLeft: 54 }}>{tables.isError ? "Failed to load" : "Loading…"}</div>}
    </>
  );
}

function TableNode({
  fullName,
  tableType,
  depth,
  onInsert,
  showSchema,
}: {
  fullName: string;
  tableType?: string;
  depth: number;
  onInsert: (t: string) => void;
  showSchema?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const table = useTable(fullName, open);
  const parts = fullName.split(".");
  return (
    <>
      <BrowserRow
        depth={depth}
        open={open}
        toggle={() => setOpen(!open)}
        icon={<TableIcon tableType={tableType} size={14} />}
        label={showSchema ? fullName : parts[parts.length - 1]}
        onClick={() => onInsert(fullName)}
        title={`Click to insert ${fullName}`}
      />
      {open &&
        (table.data?.columns ?? []).map((c) => (
          <BrowserRow
            key={c.name}
            depth={depth + 1}
            icon={<Columns3 size={13} className="muted" />}
            label={c.name}
            meta={c.type_text.toLowerCase()}
            onClick={() => onInsert(c.name)}
          />
        ))}
      {open && !table.data && <div className="tree-note" style={{ paddingLeft: 40 + depth * 14 }}>{table.isError ? "Failed to load" : "Loading…"}</div>}
    </>
  );
}

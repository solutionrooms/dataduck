// Catalog Explorer: catalogs -> schemas -> tables, with table tabs and lineage.

import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Database,
  Lock,
  Network,
  Plus,
  RefreshCw,
  Search,
  SquareTerminal,
} from "lucide-react";
import { get, post } from "../api/client";
import {
  executeStatement,
  keys,
  useCatalogs,
  useSchemas,
  useTable,
  useTables,
} from "../api/hooks";
import { useTableIndex } from "../api/search";
import type { Catalog, ColumnLineage, TableHistoryEntry, TableInfo, TableLineage } from "../api/types";
import { DagGraph, type DagNode } from "../components/DagGraph";
import { DataGrid } from "../components/DataGrid";
import { Dialog, DialogButtons } from "../components/Dialog";
import { CatalogIcon, TableIcon } from "../components/Icons";
import { EmptyState, ErrorBanner, Loading, QueryState } from "../components/States";
import { Tabs } from "../components/Tabs";
import { DateTime } from "../components/Time";
import { formatDuration, formatNumber } from "../lib/format";
import { addRecent } from "../lib/recents";
import { openSqlTab } from "../lib/sqlTabs";

interface Selection {
  catalog?: string;
  schema?: string;
  table?: string;
}

function exploreUrl(...parts: (string | undefined)[]): string {
  const segs = parts.filter((p): p is string => !!p).map(encodeURIComponent);
  return `/explore/data${segs.length ? `/${segs.join("/")}` : ""}`;
}

function tableUrl(fullName: string, extra = ""): string {
  return exploreUrl(...fullName.split(".")) + extra;
}

export function quoteName(fullName: string): string {
  return fullName
    .split(".")
    .map((p) => `"${p.replace(/"/g, '""')}"`)
    .join(".");
}

export function CatalogPage() {
  const params = useParams();
  const [catalog, schema, table] = (params["*"] ?? "").split("/").filter(Boolean);
  const sel: Selection = { catalog, schema, table };

  return (
    <div className="panel split">
      <CatalogTree sel={sel} />
      <div className="split-main">
        <div className="page">
          {table ? (
            <TableView key={`${catalog}.${schema}.${table}`} fullName={`${catalog}.${schema}.${table}`} />
          ) : schema ? (
            <SchemaView catalog={catalog} schema={schema} />
          ) : catalog ? (
            <CatalogView name={catalog} />
          ) : (
            <CatalogsOverview />
          )}
        </div>
      </div>
    </div>
  );
}

// --- tree ----------------------------------------------------------------------------------

function CatalogTree({ sel }: { sel: Selection }) {
  const [filter, setFilter] = useState("");
  const catalogs = useCatalogs();
  const qc = useQueryClient();
  const term = filter.trim().toLowerCase();
  const index = useTableIndex(term.length > 0);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.catalogs });
    qc.invalidateQueries({ queryKey: ["uc-schemas"] });
    qc.invalidateQueries({ queryKey: ["uc-tables"] });
    qc.invalidateQueries({ queryKey: ["search-index", "tables"] });
  };

  return (
    <aside className="side-tree" aria-label="Catalog tree">
      <div className="side-tree-title">
        <Link to="/explore/data" style={{ color: "inherit" }}>
          Catalog
        </Link>
        <span className="spacer" />
        <button className="icon-btn sm" title="Refresh" onClick={refresh}>
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="search-input" style={{ margin: "0 4px 10px" }}>
        <Search size={14} />
        <input
          className="input"
          placeholder="Type to search…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ height: 30 }}
        />
      </div>
      <div className="tree">
        {term ? (
          index.isPending ? (
            <div className="tree-note" style={{ paddingLeft: 8 }}>
              Searching…
            </div>
          ) : (
            <FilteredTables tables={index.data ?? []} term={term} sel={sel} />
          )
        ) : catalogs.data ? (
          catalogs.data.map((c) => <CatalogNode key={c.name} catalog={c} sel={sel} />)
        ) : catalogs.isError ? (
          <ErrorBanner error={catalogs.error} />
        ) : (
          <div className="tree-note" style={{ paddingLeft: 8 }}>
            Loading…
          </div>
        )}
      </div>
    </aside>
  );
}

function FilteredTables({ tables, term, sel }: { tables: TableInfo[]; term: string; sel: Selection }) {
  const hits = tables.filter((t) => t.full_name.toLowerCase().includes(term)).slice(0, 200);
  if (!hits.length) return <div className="tree-note" style={{ paddingLeft: 8 }}>No matching tables</div>;
  const current = sel.table ? `${sel.catalog}.${sel.schema}.${sel.table}` : "";
  return (
    <>
      {hits.map((t) => (
        <Link
          key={t.full_name}
          to={tableUrl(t.full_name)}
          className={`tree-row${current === t.full_name ? " active" : ""}`}
          style={{ paddingLeft: 8 }}
          title={t.full_name}
        >
          <TableIcon tableType={t.table_type} />
          <span className="label">{t.name}</span>
          <span className="meta truncate">
            {t.catalog_name}.{t.schema_name}
          </span>
        </Link>
      ))}
    </>
  );
}

function Twisty({ open, toggle }: { open: boolean; toggle: () => void }) {
  return (
    <span
      className="twisty"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        toggle();
      }}
    >
      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
    </span>
  );
}

function CatalogNode({ catalog, sel }: { catalog: Catalog; sel: Selection }) {
  const isSel = sel.catalog === catalog.name;
  const [open, setOpen] = useState(isSel);
  useEffect(() => {
    if (isSel) setOpen(true);
  }, [isSel]);
  const schemas = useSchemas(catalog.name, open);
  return (
    <>
      <Link
        to={exploreUrl(catalog.name)}
        className={`tree-row${isSel && !sel.schema ? " active" : ""}`}
        style={{ paddingLeft: 4 }}
      >
        <Twisty open={open} toggle={() => setOpen((o) => !o)} />
        <CatalogIcon />
        <span className="label">{catalog.name}</span>
        {catalog.catalog_type === "SYSTEM_CATALOG" && <Lock size={11} className="muted" />}
      </Link>
      {open &&
        (schemas.data ? (
          schemas.data.length ? (
            schemas.data.map((s) => <SchemaNode key={s.name} catalog={catalog.name} schema={s.name} sel={sel} />)
          ) : (
            <div className="tree-note" style={{ paddingLeft: 46 }}>
              No schemas
            </div>
          )
        ) : (
          <div className="tree-note" style={{ paddingLeft: 46 }}>
            {schemas.isError ? "Failed to load" : "Loading…"}
          </div>
        ))}
    </>
  );
}

function SchemaNode({ catalog, schema, sel }: { catalog: string; schema: string; sel: Selection }) {
  const isSel = sel.catalog === catalog && sel.schema === schema;
  const [open, setOpen] = useState(isSel);
  useEffect(() => {
    if (isSel) setOpen(true);
  }, [isSel]);
  const tables = useTables(catalog, schema, open);
  return (
    <>
      <Link
        to={exploreUrl(catalog, schema)}
        className={`tree-row${isSel && !sel.table ? " active" : ""}`}
        style={{ paddingLeft: 20 }}
      >
        <Twisty open={open} toggle={() => setOpen((o) => !o)} />
        <Database size={15} className="icon-folder" />
        <span className="label">{schema}</span>
      </Link>
      {open &&
        (tables.data ? (
          tables.data.length ? (
            tables.data.map((t) => (
              <Link
                key={t.name}
                to={exploreUrl(catalog, schema, t.name)}
                className={`tree-row${isSel && sel.table === t.name ? " active" : ""}`}
                style={{ paddingLeft: 58 }}
                title={t.full_name}
              >
                <TableIcon tableType={t.table_type} />
                <span className="label">{t.name}</span>
              </Link>
            ))
          ) : (
            <div className="tree-note" style={{ paddingLeft: 62 }}>
              No tables
            </div>
          )
        ) : (
          <div className="tree-note" style={{ paddingLeft: 62 }}>
            {tables.isError ? "Failed to load" : "Loading…"}
          </div>
        ))}
    </>
  );
}

// --- catalogs overview -----------------------------------------------------------------------

function CreateDialog({
  kind,
  catalog,
  onClose,
}: {
  kind: "catalog" | "schema";
  catalog?: string;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [comment, setComment] = useState("");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const create = useMutation({
    mutationFn: () =>
      kind === "catalog"
        ? post("/api/2.1/unity-catalog/catalogs", { name: name.trim(), comment: comment || undefined })
        : post("/api/2.1/unity-catalog/schemas", { catalog_name: catalog, name: name.trim(), comment: comment || undefined }),
    onSuccess: () => {
      if (kind === "catalog") {
        qc.invalidateQueries({ queryKey: keys.catalogs });
        navigate(exploreUrl(name.trim()));
      } else {
        qc.invalidateQueries({ queryKey: keys.schemas(catalog!) });
        navigate(exploreUrl(catalog, name.trim()));
      }
      qc.invalidateQueries({ queryKey: ["search-index", "tables"] });
      onClose();
    },
  });
  return (
    <Dialog
      title={kind === "catalog" ? "Create a new catalog" : `Create a schema in ${catalog}`}
      onClose={onClose}
      onSubmit={() => name.trim() && create.mutate()}
      footer={<DialogButtons onCancel={onClose} confirmLabel="Create" busy={create.isPending} disabled={!name.trim()} />}
    >
      <div className="field">
        <label htmlFor="uc-name">{kind === "catalog" ? "Catalog name" : "Schema name"}</label>
        <input id="uc-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="uc-comment">Comment (optional)</label>
        <input id="uc-comment" className="input" value={comment} onChange={(e) => setComment(e.target.value)} />
      </div>
    </Dialog>
  );
}

function CatalogsOverview() {
  const catalogs = useCatalogs();
  const [creating, setCreating] = useState(false);
  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Catalog</h1>
        <div className="page-actions">
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            <Plus size={14} />
            Create catalog
          </button>
        </div>
      </div>
      <p className="secondary" style={{ marginTop: -6, marginBottom: 16 }}>
        Browse catalogs, schemas and tables in the lakehouse. <span className="mono">sys</span> holds read-only system
        tables.
      </p>
      {catalogs.data ? (
        catalogs.data.length ? (
          <table className="dt">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Comment</th>
                <th>Storage root</th>
                <th>Created at</th>
              </tr>
            </thead>
            <tbody>
              {catalogs.data.map((c) => (
                <tr key={c.name}>
                  <td>
                    <div className="name-cell">
                      <CatalogIcon />
                      <Link to={exploreUrl(c.name)}>{c.name}</Link>
                    </div>
                  </td>
                  <td className="secondary">{c.catalog_type === "SYSTEM_CATALOG" ? "System" : "Managed"}</td>
                  <td className="secondary">{c.comment || "–"}</td>
                  <td className="secondary mono">{c.storage_root ?? "–"}</td>
                  <td className="secondary">
                    <DateTime value={c.created_at} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState title="No catalogs" />
        )
      ) : (
        <QueryState query={catalogs} what="Unity Catalog" />
      )}
      {creating && <CreateDialog kind="catalog" onClose={() => setCreating(false)} />}
    </>
  );
}

// --- catalog ---------------------------------------------------------------------------------

function CatalogView({ name }: { name: string }) {
  const catalogs = useCatalogs();
  const schemas = useSchemas(name);
  const [creating, setCreating] = useState(false);
  const info = catalogs.data?.find((c) => c.name === name);
  const system = info?.catalog_type === "SYSTEM_CATALOG";

  return (
    <>
      <div className="breadcrumb">
        <Link to="/explore/data">Catalog</Link>
        <ChevronRight size={14} />
      </div>
      <div className="page-header">
        <h1 className="page-title">
          <CatalogIcon size={22} />
          <span className="title-text">{name}</span>
          {system && <span className="badge">System · read-only</span>}
        </h1>
        <div className="page-actions">
          <button className="btn btn-primary" onClick={() => setCreating(true)} disabled={system}>
            <Plus size={14} />
            Create schema
          </button>
        </div>
      </div>
      {info?.comment && <p className="secondary" style={{ marginTop: -6 }}>{info.comment}</p>}
      <div className="home-grid">
        <div>
          <div className="section-title">Schemas</div>
          {schemas.data ? (
            schemas.data.length ? (
              <table className="dt">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Comment</th>
                  </tr>
                </thead>
                <tbody>
                  {schemas.data.map((s) => (
                    <tr key={s.name}>
                      <td>
                        <div className="name-cell">
                          <Database size={16} className="icon-folder" />
                          <Link to={exploreUrl(name, s.name)}>{s.name}</Link>
                        </div>
                      </td>
                      <td className="secondary">{s.comment || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState compact title="No schemas in this catalog" />
            )
          ) : (
            <QueryState query={schemas} what="Schemas" />
          )}
        </div>
        {info && (
          <div className="card">
            <div className="card-title">About this catalog</div>
            <dl className="kv">
              <dt>Type</dt>
              <dd>{info.catalog_type ?? "–"}</dd>
              <dt>Owner</dt>
              <dd>{info.owner ?? "–"}</dd>
              <dt>Storage root</dt>
              <dd className="mono">{info.storage_root ?? "–"}</dd>
              <dt>Created</dt>
              <dd>
                <DateTime value={info.created_at} />
              </dd>
            </dl>
          </div>
        )}
      </div>
      {creating && <CreateDialog kind="schema" catalog={name} onClose={() => setCreating(false)} />}
    </>
  );
}

// --- schema ----------------------------------------------------------------------------------

function SchemaView({ catalog, schema }: { catalog: string; schema: string }) {
  const tables = useTables(catalog, schema);
  const [q, setQ] = useState("");
  const rows = (tables.data ?? []).filter((t) => t.name.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <>
      <div className="breadcrumb">
        <Link to="/explore/data">Catalog</Link>
        <ChevronRight size={14} />
        <Link to={exploreUrl(catalog)}>{catalog}</Link>
        <ChevronRight size={14} />
      </div>
      <div className="page-header">
        <h1 className="page-title">
          <Database size={22} className="icon-folder" />
          <span className="title-text">{schema}</span>
        </h1>
      </div>
      <div className="toolbar">
        <div className="search-input" style={{ width: 260 }}>
          <Search size={14} />
          <input className="input" placeholder="Filter tables" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {tables.data ? (
        rows.length ? (
          <table className="dt">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Columns</th>
                <th>Comment</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.name}>
                  <td>
                    <div className="name-cell">
                      <TableIcon tableType={t.table_type} />
                      <Link to={exploreUrl(catalog, schema, t.name)}>{t.name}</Link>
                    </div>
                  </td>
                  <td className="secondary">{t.table_type === "VIEW" ? "View" : t.table_type === "MANAGED" ? "Managed" : t.table_type ?? "–"}</td>
                  <td className="secondary">{String(t.properties?.["duckbricks.column_count"] ?? "–")}</td>
                  <td className="secondary">{t.comment || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState
            compact={tables.data.length > 0}
            title={tables.data.length ? "No matching tables" : "No tables in this schema"}
            description={tables.data.length ? undefined : "Tables created by SQL, jobs or dbt appear here."}
          />
        )
      ) : (
        <QueryState query={tables} what="Tables" />
      )}
    </>
  );
}

// --- table -----------------------------------------------------------------------------------

type TableTab = "overview" | "sample" | "details" | "history" | "lineage";

function TableView({ fullName }: { fullName: string }) {
  const [catalog, schema, name] = fullName.split(".");
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as TableTab) || "overview";
  const navigate = useNavigate();
  const table = useTable(fullName);

  useEffect(() => {
    if (table.data) addRecent({ kind: "table", title: name, url: tableUrl(fullName), subtitle: fullName });
  }, [table.data, fullName, name]);

  const setTab = (t: TableTab, extra: Record<string, string> = {}) => {
    const next = new URLSearchParams();
    if (t !== "overview") next.set("tab", t);
    for (const [k, v] of Object.entries(extra)) next.set(k, v);
    setParams(next);
  };

  const t = table.data;
  return (
    <>
      <div className="breadcrumb">
        <Link to="/explore/data">Catalog</Link>
        <ChevronRight size={14} />
        <Link to={exploreUrl(catalog)}>{catalog}</Link>
        <ChevronRight size={14} />
        <Link to={exploreUrl(catalog, schema)}>{schema}</Link>
        <ChevronRight size={14} />
      </div>
      <div className="page-header" style={{ marginBottom: 6 }}>
        <h1 className="page-title">
          <TableIcon tableType={t?.table_type} size={22} />
          <span className="title-text">{name}</span>
          {t?.table_type && <span className="badge">{t.table_type}</span>}
        </h1>
        <div className="page-actions">
          <button
            className="btn"
            onClick={() => {
              openSqlTab(`SELECT * FROM ${fullName} LIMIT 100;`, name, catalog, schema);
              navigate("/sql/editor");
            }}
          >
            <SquareTerminal size={14} />
            Open in SQL editor
          </button>
        </div>
      </div>
      <p className="secondary" style={{ margin: "0 0 12px" }}>
        {t?.comment || <span className="muted">No description</span>}
      </p>
      {!t ? (
        <QueryState query={table} what="Table metadata" />
      ) : (
        <>
          <Tabs<TableTab>
            items={[
              { key: "overview", label: "Overview" },
              { key: "sample", label: "Sample data" },
              { key: "details", label: "Details" },
              { key: "history", label: "History" },
              { key: "lineage", label: "Lineage" },
            ]}
            active={tab}
            onChange={(k) => setTab(k)}
          />
          {tab === "overview" && <ColumnsTab table={t} onColumnLineage={(c) => setTab("lineage", { column: c })} />}
          {tab === "sample" && <SampleTab fullName={fullName} />}
          {tab === "details" && <DetailsTab table={t} />}
          {tab === "history" && <HistoryTab fullName={fullName} />}
          {tab === "lineage" && (
            <LineageTab
              table={t}
              column={params.get("column")}
              setColumn={(c) => setTab("lineage", c ? { column: c } : {})}
            />
          )}
        </>
      )}
    </>
  );
}

function ColumnsTab({ table, onColumnLineage }: { table: TableInfo; onColumnLineage: (c: string) => void }) {
  const [q, setQ] = useState("");
  const cols = (table.columns ?? []).filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase()));
  if (!table.columns?.length) return <EmptyState compact title="No columns" />;
  return (
    <>
      <div className="toolbar">
        <div className="search-input" style={{ width: 260 }}>
          <Search size={14} />
          <input className="input" placeholder="Filter columns" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <span className="muted">{formatNumber(table.columns.length)} columns</span>
      </div>
      <table className="dt compact">
        <thead>
          <tr>
            <th>Column</th>
            <th>Type</th>
            <th>Nullable</th>
            <th>Comment</th>
            <th className="actions-cell" />
          </tr>
        </thead>
        <tbody>
          {cols.map((c) => (
            <tr key={c.name}>
              <td className="mono">{c.name}</td>
              <td className="secondary mono">{c.type_text}</td>
              <td className="secondary">{c.nullable === false ? "No" : "Yes"}</td>
              <td className="secondary">{c.comment || "–"}</td>
              <td className="actions-cell">
                <div className="row-actions">
                  <button className="btn btn-sm btn-ghost" onClick={() => onColumnLineage(c.name)} title="Column lineage">
                    <Network size={13} />
                    Lineage
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function SampleTab({ fullName }: { fullName: string }) {
  const sample = useQuery({
    queryKey: ["sample", fullName],
    queryFn: () => executeStatement({ statement: `SELECT * FROM ${quoteName(fullName)} LIMIT 50`, read_only: true, row_limit: 50 }),
    staleTime: 60_000,
    retry: false,
  });
  if (!sample.data) return <QueryState query={sample} what="The SQL statements API" loadingLabel="Running sample query…" />;
  const { response, rows, durationMs } = sample.data;
  if (response.status.state !== "SUCCEEDED") {
    return <ErrorBanner error={new Error(response.status.error?.message ?? `Statement ${response.status.state}`)} onRetry={() => sample.refetch()} />;
  }
  const columns = response.manifest?.schema.columns ?? [];
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div className="grid-footer" style={{ borderTop: "none", borderBottom: "1px solid var(--border)" }}>
        <span>
          {formatNumber(rows.length)} row{rows.length === 1 ? "" : "s"} · {formatDuration(durationMs)}
        </span>
        <span className="spacer" />
        <button className="btn btn-sm btn-ghost" onClick={() => sample.refetch()} disabled={sample.isFetching}>
          <RefreshCw size={12} className={sample.isFetching ? "spin" : undefined} />
          Refresh
        </button>
      </div>
      {rows.length ? (
        <DataGrid columns={columns} rows={rows} height={Math.min(560, 34 + rows.length * 28 + 4)} />
      ) : (
        <EmptyState compact title="The table is empty" />
      )}
    </div>
  );
}

function DetailsTab({ table }: { table: TableInfo }) {
  const props = Object.entries(table.properties ?? {});
  return (
    <>
      <div className="card">
        <dl className="kv">
          <dt>Full name</dt>
          <dd className="mono">{table.full_name}</dd>
          <dt>Table type</dt>
          <dd>{table.table_type ?? "–"}</dd>
          <dt>Data source format</dt>
          <dd>{table.data_source_format ?? "–"}</dd>
          <dt>Owner</dt>
          <dd>{table.owner ?? "–"}</dd>
          <dt>Storage location</dt>
          <dd className="mono">{table.storage_location ?? "–"}</dd>
          {table.created_at !== undefined && (
            <>
              <dt>Created at</dt>
              <dd>
                <DateTime value={table.created_at} />
              </dd>
            </>
          )}
          {table.updated_at !== undefined && (
            <>
              <dt>Updated at</dt>
              <dd>
                <DateTime value={table.updated_at} />
              </dd>
            </>
          )}
        </dl>
      </div>
      {props.length > 0 && (
        <>
          <div className="section-title">Properties</div>
          <table className="dt compact">
            <thead>
              <tr>
                <th>Key</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {props.map(([k, v]) => (
                <tr key={k}>
                  <td className="mono">{k}</td>
                  <td className="mono secondary">{typeof v === "string" ? v : JSON.stringify(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {table.view_definition && (
        <>
          <div className="section-title">View definition</div>
          <pre className="code-block">{table.view_definition}</pre>
        </>
      )}
    </>
  );
}

function HistoryTab({ fullName }: { fullName: string }) {
  const history = useQuery({
    queryKey: ["uc-history", fullName],
    queryFn: () =>
      get<{ history?: TableHistoryEntry[] }>(`/api/2.1/unity-catalog/tables/${encodeURIComponent(fullName)}/history`).then(
        (r) => r.history ?? [],
      ),
  });
  if (!history.data) return <QueryState query={history} what="Table history" />;
  if (!history.data.length) {
    return <EmptyState compact title="No history" description="Views and system tables have no version history." />;
  }
  return (
    <table className="dt compact">
      <thead>
        <tr>
          <th>Version</th>
          <th>Timestamp</th>
          <th>Operation</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {[...history.data]
          .sort((a, b) => b.version - a.version)
          .map((h) => {
            const { version, timestamp, operation, ...rest } = h;
            const extra = Object.keys(rest).length ? JSON.stringify(rest) : "";
            return (
              <tr key={version}>
                <td className="mono">{version}</td>
                <td>
                  <DateTime value={timestamp} />
                </td>
                <td>{operation}</td>
                <td className="mono secondary truncate" style={{ maxWidth: 480 }} title={extra}>
                  {extra || "–"}
                </td>
              </tr>
            );
          })}
      </tbody>
    </table>
  );
}

// --- lineage ---------------------------------------------------------------------------------

function lineageNodes(center: string, data: TableLineage): DagNode[] {
  const names = new Set([center, ...data.upstreams.map((u) => u.name), ...data.downstreams.map((d) => d.name)]);
  const nodes = new Map<string, DagNode>();
  const add = (name: string, dependsOn: string[], kind: DagNode["kind"]) => {
    const existing = nodes.get(name);
    if (existing) {
      existing.dependsOn = [...new Set([...existing.dependsOn, ...dependsOn])];
      return;
    }
    const short = name.split(".").pop() ?? name;
    nodes.set(name, {
      key: name,
      dependsOn,
      title: short,
      subtitle: name,
      kind,
      icon: <TableIcon size={15} />,
    });
  };
  add(center, [], "running");
  for (const u of data.upstreams) add(u.name, [], "none");
  for (const u of data.upstreams) {
    // An upstream at depth > 1 feeds the intermediate table named by `via` when we know it.
    const target = u.via && names.has(u.via) && u.via !== u.name ? u.via : center;
    nodes.get(target)!.dependsOn.push(u.name);
  }
  for (const d of data.downstreams) {
    const source = d.via && names.has(d.via) && d.via !== d.name ? d.via : center;
    add(d.name, [source], "none");
  }
  for (const n of nodes.values()) n.dependsOn = [...new Set(n.dependsOn)].filter((k) => k !== n.key);
  return [...nodes.values()];
}

function LineageTab({
  table,
  column,
  setColumn,
}: {
  table: TableInfo;
  column: string | null;
  setColumn: (c: string | null) => void;
}) {
  const navigate = useNavigate();
  const lineage = useQuery({
    queryKey: ["lineage-table", table.full_name],
    queryFn: () => get<TableLineage>("/api/duckbricks/lineage/table", { name: table.full_name }),
  });
  const nodes = useMemo(
    () => (lineage.data ? lineageNodes(table.full_name, { ...lineage.data, upstreams: lineage.data.upstreams ?? [], downstreams: lineage.data.downstreams ?? [] }) : []),
    [lineage.data, table.full_name],
  );

  return (
    <>
      {lineage.data ? (
        lineage.data.upstreams?.length || lineage.data.downstreams?.length ? (
          <>
            <DagGraph
              nodes={nodes}
              selected={table.full_name}
              onSelect={(k) => k !== table.full_name && navigate(tableUrl(k, "?tab=lineage"))}
            />
            <div className="home-grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 16 }}>
              <LineageList title="Upstream" items={lineage.data.upstreams ?? []} />
              <LineageList title="Downstream" items={lineage.data.downstreams ?? []} />
            </div>
          </>
        ) : (
          <EmptyState
            compact
            icon={<Network size={22} />}
            title="No lineage recorded"
            description="Lineage is captured when jobs, dbt or SQL statements read from or write to this table."
          />
        )
      ) : (
        <QueryState query={lineage} what="Table lineage" />
      )}
      <ColumnLineagePanel table={table} column={column} setColumn={setColumn} />
    </>
  );
}

function LineageList({ title, items }: { title: string; items: TableLineage["upstreams"] }) {
  return (
    <div>
      <div className="section-title">
        {title} <span className="badge">{items.length}</span>
      </div>
      {items.length ? (
        <table className="dt compact">
          <thead>
            <tr>
              <th>Table</th>
              <th>Depth</th>
              <th>Via</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {[...items]
              .sort((a, b) => (a.depth ?? 1) - (b.depth ?? 1))
              .map((u, i) => (
                <tr key={`${u.name}-${i}`}>
                  <td>
                    <div className="name-cell">
                      <TableIcon />
                      <Link to={tableUrl(u.name, "?tab=lineage")}>{u.name}</Link>
                    </div>
                  </td>
                  <td className="secondary">{u.depth ?? 1}</td>
                  <td className="secondary">{u.via ?? "–"}</td>
                  <td className="secondary">{u.source ?? "–"}</td>
                </tr>
              ))}
          </tbody>
        </table>
      ) : (
        <div className="muted">None</div>
      )}
    </div>
  );
}

function ColumnLineagePanel({
  table,
  column,
  setColumn,
}: {
  table: TableInfo;
  column: string | null;
  setColumn: (c: string | null) => void;
}) {
  const lineage = useQuery({
    queryKey: ["lineage-column", table.full_name, column],
    queryFn: () => get<ColumnLineage>("/api/duckbricks/lineage/column", { table: table.full_name, column: column! }),
    enabled: !!column,
  });
  return (
    <>
      <div className="section-title" style={{ marginTop: 28 }}>
        Column lineage
      </div>
      <div className="toolbar">
        <select className="select" value={column ?? ""} onChange={(e) => setColumn(e.target.value || null)} style={{ minWidth: 240 }}>
          <option value="">Select a column…</option>
          {(table.columns ?? []).map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
      </div>
      {!column ? (
        <div className="muted">Pick a column, or use “Lineage” on a column in the Overview tab.</div>
      ) : lineage.data ? (
        <div className="home-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          {(["upstreams", "downstreams"] as const).map((dir) => {
            const items = lineage.data[dir] ?? [];
            return (
              <div key={dir}>
                <div className="section-title" style={{ fontSize: 13 }}>
                  {dir === "upstreams" ? "Upstream columns" : "Downstream columns"} <span className="badge">{items.length}</span>
                </div>
                {items.length ? (
                  <table className="dt compact">
                    <thead>
                      <tr>
                        <th>Column</th>
                        <th>Depth</th>
                        <th>Via</th>
                        <th>Evolution</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((c, i) => (
                        <tr key={`${c.table}.${c.column}-${i}`}>
                          <td className="mono">
                            <Link to={tableUrl(c.table, `?tab=lineage&column=${encodeURIComponent(c.column)}`)}>
                              {c.table}.{c.column}
                            </Link>
                          </td>
                          <td className="secondary">{c.depth ?? 1}</td>
                          <td className="secondary mono">{c.via ?? "–"}</td>
                          <td className="secondary">{c.evolution ?? "–"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="muted">None</div>
                )}
              </div>
            );
          })}
        </div>
      ) : lineage.isPending ? (
        <Loading compact />
      ) : (
        <QueryState query={lineage} what="Column lineage" compact />
      )}
    </>
  );
}

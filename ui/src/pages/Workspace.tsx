// Workspace browser: folder tree, breadcrumb, filters and the object table; Favorites; Trash.

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Copy,
  EllipsisVertical,
  ExternalLink,
  FileCode,
  FolderOpen,
  FolderPlus,
  House,
  NotebookText,
  RotateCcw,
  Search,
  Star,
  Trash,
  Upload,
  FolderTree,
} from "lucide-react";
import { get, post } from "../api/client";
import { keys, useFavorites, useHomePath, useStatus, useWorkspaceList } from "../api/hooks";
import type { TrashItem, WorkspaceObject } from "../api/types";
import { ConfirmDialog } from "../components/Dialog";
import { ObjectIcon } from "../components/Icons";
import { Menu, type MenuItem } from "../components/Menu";
import { EmptyState, QueryState } from "../components/States";
import { DateTime, RelTime } from "../components/Time";
import {
  CreateObjectDialog,
  pickFiles,
  uploadFiles,
  useInvalidateWorkspace,
  type CreateKind,
} from "../components/WorkspaceDialogs";
import { addRecent } from "../lib/recents";
import {
  basename,
  browseUrl,
  dirname,
  editorUrl,
  notebookUrl,
  objectTypeLabel,
  pathFromSplat,
} from "../lib/paths";
import { toast } from "../lib/toast";

type View = "folder" | "favorites" | "trash";

export function WorkspacePage() {
  const params = useParams();
  const [search] = useSearchParams();
  const home = useHomePath();
  const navigate = useNavigate();
  const view: View = search.get("view") === "trash" ? "trash" : search.get("view") === "favorites" ? "favorites" : "folder";
  const path = pathFromSplat(params["*"]);

  // Bare /browse opens the home folder, like the Workspace sidebar entry.
  useEffect(() => {
    if (view === "folder" && path === "/" && home && !search.has("root")) navigate(browseUrl(home), { replace: true });
  }, [view, path, home, search, navigate]);

  return (
    <div className="panel split">
      <WorkspaceTree current={path} view={view} home={home} />
      <div className="split-main">
        <div className="page">
          {view === "trash" ? <TrashView /> : view === "favorites" ? <FavoritesView /> : <FolderView path={path} />}
        </div>
      </div>
    </div>
  );
}

// --- left tree ---------------------------------------------------------------------------

function WorkspaceTree({ current, view, home }: { current: string; view: View; home: string | null }) {
  return (
    <aside className="side-tree" aria-label="Workspace tree">
      <div className="side-tree-title">Workspace</div>
      <div className="tree">
        <Link
          to={home ? browseUrl(home) : "/browse"}
          className={`tree-row${view === "folder" && current === home ? " active" : ""}`}
          style={{ paddingLeft: 26 }}
        >
          <House size={16} />
          <span className="label">Home</span>
        </Link>
        <TreeFolder path="/" label="Workspace" depth={0} current={view === "folder" ? current : ""} icon={<FolderTree size={16} />} />
        <div className="tree-sep" />
        <Link to="/browse?view=favorites" className={`tree-row${view === "favorites" ? " active" : ""}`} style={{ paddingLeft: 26 }}>
          <Star size={16} />
          <span className="label">Favorites</span>
        </Link>
        <Link to="/browse?view=trash" className={`tree-row${view === "trash" ? " active" : ""}`} style={{ paddingLeft: 26 }}>
          <Trash size={16} />
          <span className="label">Trash</span>
        </Link>
      </div>
    </aside>
  );
}

function TreeFolder({
  path,
  label,
  depth,
  current,
  icon,
}: {
  path: string;
  label: string;
  depth: number;
  current: string;
  icon?: ReactNode;
}) {
  const isAncestor = path === "/" ? current !== "" : current.startsWith(`${path}/`);
  const [open, setOpen] = useState(isAncestor || path === "/");
  useEffect(() => {
    if (isAncestor) setOpen(true);
  }, [isAncestor]);
  const list = useWorkspaceList(path, open);
  const dirs = (list.data ?? [])
    .filter((o) => o.object_type === "DIRECTORY")
    .sort((a, b) => basename(a.path).localeCompare(basename(b.path)));
  const to = path === "/" ? "/browse?root=1" : browseUrl(path);

  return (
    <>
      <Link
        to={to}
        className={`tree-row${current === path ? " active" : ""}`}
        style={{ paddingLeft: 4 + depth * 14 }}
        title={path}
      >
        <span
          className="twisty"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOpen((o) => !o);
          }}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        {icon ?? (open ? <FolderOpen size={16} className="icon-folder" /> : <ObjectIcon type="DIRECTORY" />)}
        <span className="label">{label}</span>
      </Link>
      {open && (
        <>
          {list.isPending && (
            <div className="tree-note" style={{ paddingLeft: 26 + (depth + 1) * 14 }}>
              Loading…
            </div>
          )}
          {list.isError && (
            <div className="tree-note" style={{ paddingLeft: 26 + (depth + 1) * 14 }}>
              Failed to load
            </div>
          )}
          {dirs.map((d) => (
            <TreeFolder key={d.path} path={d.path} label={basename(d.path)} depth={depth + 1} current={current} />
          ))}
        </>
      )}
    </>
  );
}

// --- shared row actions --------------------------------------------------------------------

function useFavoriteToggle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ path, favorite }: { path: string; favorite: boolean }) =>
      post("/api/duckbricks/workspace/favorites", { path, favorite }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.wsFavorites }),
  });
}

function useMoveToTrash() {
  const qc = useQueryClient();
  const invalidate = useInvalidateWorkspace();
  return useMutation({
    mutationFn: (path: string) => post("/api/2.0/workspace/delete", { path, recursive: true }).then(() => path),
    onSuccess: (path) => {
      invalidate(dirname(path));
      qc.invalidateQueries({ queryKey: keys.wsTrash });
      qc.invalidateQueries({ queryKey: keys.wsFavorites });
      toast.success(`Moved ${basename(path)} to Trash`, {
        label: "Undo",
        onClick: async () => {
          try {
            const { items = [] } = await get<{ items?: TrashItem[] }>("/api/duckbricks/workspace/trash");
            const item = items
              .filter((i) => i.path === path)
              .sort((a, b) => (b.deleted_at ?? 0) - (a.deleted_at ?? 0))[0];
            if (!item) return;
            await post("/api/duckbricks/workspace/trash/restore", { trash_id: item.trash_id });
            invalidate(dirname(path));
            qc.invalidateQueries({ queryKey: keys.wsTrash });
          } catch (err) {
            toast.error(err);
          }
        },
      });
    },
  });
}

function ObjectName({ o }: { o: WorkspaceObject }) {
  const { data: status } = useStatus();
  const name = basename(o.path);
  if (o.object_type === "DIRECTORY") return <Link to={browseUrl(o.path)}>{name}</Link>;
  if (o.object_type === "NOTEBOOK") {
    const href = notebookUrl(status?.links.notebooks, o.path);
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        onClick={() =>
          addRecent({ kind: "notebook", title: name, url: editorUrl(o.path), href, subtitle: dirname(o.path) })
        }
      >
        {name}
      </a>
    );
  }
  return <Link to={editorUrl(o.path)}>{name}</Link>;
}

function rowMenu(
  o: WorkspaceObject,
  isFav: boolean,
  notebooksBase: string | undefined,
  actions: { favorite: () => void; trash: () => void; navigate: (to: string) => void },
): MenuItem[] {
  const items: MenuItem[] = [];
  if (o.object_type === "DIRECTORY") items.push({ label: "Open", icon: <FolderOpen size={16} />, to: browseUrl(o.path) });
  if (o.object_type === "NOTEBOOK") {
    items.push({ label: "Open in marimo", icon: <ExternalLink size={16} />, href: notebookUrl(notebooksBase, o.path) });
    items.push({ label: "Open as file", icon: <FileCode size={16} />, to: editorUrl(o.path) });
  }
  if (o.object_type === "FILE") items.push({ label: "Open", icon: <FileCode size={16} />, to: editorUrl(o.path) });
  items.push({
    label: "Copy path",
    icon: <Copy size={16} />,
    onClick: () => {
      navigator.clipboard?.writeText(o.path).then(
        () => toast.info("Path copied"),
        () => toast.error("Could not copy to the clipboard"),
      );
    },
  });
  items.push({ label: isFav ? "Remove from favorites" : "Add to favorites", icon: <Star size={16} />, onClick: actions.favorite });
  items.push({ separator: true, label: "" });
  items.push({ label: "Move to Trash", icon: <Trash size={16} />, danger: true, onClick: actions.trash });
  return items;
}

type SortKey = "name" | "type" | "created" | "modified";

function sortObjects(list: WorkspaceObject[], key: SortKey, dir: 1 | -1): WorkspaceObject[] {
  const rank = (o: WorkspaceObject) => (o.object_type === "DIRECTORY" ? 0 : 1);
  return [...list].sort((a, b) => {
    if (key === "name" || key === "type") {
      const r = rank(a) - rank(b);
      if (r !== 0) return r;
    }
    let cmp = 0;
    if (key === "name") cmp = basename(a.path).localeCompare(basename(b.path), undefined, { numeric: true });
    else if (key === "type") cmp = objectTypeLabel(a).localeCompare(objectTypeLabel(b));
    else if (key === "created") cmp = (a.created_at ?? 0) - (b.created_at ?? 0);
    else cmp = (a.modified_at ?? 0) - (b.modified_at ?? 0);
    return cmp * dir;
  });
}

function SortHeader({
  label,
  k,
  sort,
  setSort,
}: {
  label: string;
  k: SortKey;
  sort: { key: SortKey; dir: 1 | -1 };
  setSort: (s: { key: SortKey; dir: 1 | -1 }) => void;
}) {
  const active = sort.key === k;
  return (
    <th className="sortable" onClick={() => setSort({ key: k, dir: active ? (sort.dir === 1 ? -1 : 1) : 1 })}>
      {label}
      {active && <span className="sort-ind">{sort.dir === 1 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}</span>}
    </th>
  );
}

function ObjectTable({ objects, showLocation }: { objects: WorkspaceObject[]; showLocation?: boolean }) {
  const favorites = useFavorites();
  const favSet = useMemo(() => new Set((favorites.data ?? []).map((f) => f.path)), [favorites.data]);
  const toggleFav = useFavoriteToggle();
  const trash = useMoveToTrash();
  const navigate = useNavigate();
  const { data: status } = useStatus();
  const [confirm, setConfirm] = useState<WorkspaceObject | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "name", dir: 1 });
  const sorted = useMemo(() => sortObjects(objects, sort.key, sort.dir), [objects, sort]);

  return (
    <>
      <div className="table-wrap">
        <table className="dt">
          <thead>
            <tr>
              <SortHeader label="Name" k="name" sort={sort} setSort={setSort} />
              <SortHeader label="Type" k="type" sort={sort} setSort={setSort} />
              {showLocation && <th>Location</th>}
              <SortHeader label="Created at" k="created" sort={sort} setSort={setSort} />
              <SortHeader label="Last updated at" k="modified" sort={sort} setSort={setSort} />
              <th className="actions-cell" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((o) => {
              const isFav = favSet.has(o.path);
              return (
                <tr key={o.path}>
                  <td>
                    <div className="name-cell">
                      <ObjectIcon type={o.object_type} path={o.path} />
                      <span className="truncate">
                        <ObjectName o={o} />
                      </span>
                    </div>
                  </td>
                  <td className="secondary">{objectTypeLabel(o)}</td>
                  {showLocation && (
                    <td className="secondary">
                      <Link to={browseUrl(dirname(o.path))} className="secondary">
                        {dirname(o.path)}
                      </Link>
                    </td>
                  )}
                  <td className="secondary">
                    <DateTime value={o.created_at} />
                  </td>
                  <td className="secondary">
                    <DateTime value={o.modified_at} />
                  </td>
                  <td className="actions-cell">
                    <div className={`row-actions${isFav ? " visible" : ""}`}>
                      <button
                        className={`icon-btn star-btn${isFav ? " on" : ""}`}
                        title={isFav ? "Remove from favorites" : "Add to favorites"}
                        onClick={() => toggleFav.mutate({ path: o.path, favorite: !isFav })}
                      >
                        <Star size={15} fill={isFav ? "currentColor" : "none"} />
                      </button>
                      <Menu
                        trigger={({ onClick }) => (
                          <button className="icon-btn" onClick={onClick} aria-label="More actions">
                            <EllipsisVertical size={16} />
                          </button>
                        )}
                        items={rowMenu(o, isFav, status?.links.notebooks, {
                          favorite: () => toggleFav.mutate({ path: o.path, favorite: !isFav }),
                          trash: () => setConfirm(o),
                          navigate,
                        })}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {confirm && (
        <ConfirmDialog
          title="Move to Trash?"
          message={
            <>
              <span className="mono">{basename(confirm.path)}</span>
              {confirm.object_type === "DIRECTORY" ? " and everything in it" : ""} will be moved to Trash. You can
              restore it from Trash later.
            </>
          }
          confirmLabel="Move to Trash"
          danger
          busy={trash.isPending}
          onClose={() => setConfirm(null)}
          onConfirm={() => trash.mutate(confirm.path, { onSettled: () => setConfirm(null) })}
        />
      )}
    </>
  );
}

// --- folder view ---------------------------------------------------------------------------

const MODIFIED_WINDOWS: Record<string, number> = {
  "24h": 86_400_000,
  "7d": 7 * 86_400_000,
  "30d": 30 * 86_400_000,
};

function Breadcrumb({ path }: { path: string }) {
  const segments = path.split("/").filter(Boolean);
  const parents = segments.slice(0, -1);
  return (
    <div className="breadcrumb">
      <Link to="/browse?root=1">Workspace</Link>
      {parents.map((seg, i) => (
        <span key={i} className="row" style={{ gap: 4 }}>
          <ChevronRight size={14} />
          <Link to={browseUrl(`/${segments.slice(0, i + 1).join("/")}`)}>{seg}</Link>
        </span>
      ))}
      {segments.length > 0 && <ChevronRight size={14} />}
    </div>
  );
}

function FolderView({ path }: { path: string }) {
  const list = useWorkspaceList(path);
  const favorites = useFavorites();
  const toggleFav = useFavoriteToggle();
  const invalidate = useInvalidateWorkspace();
  const [creating, setCreating] = useState<CreateKind | null>(null);
  const [q, setQ] = useState("");
  const [type, setType] = useState("all");
  const [modified, setModified] = useState("any");
  useEffect(() => setQ(""), [path]);

  const isRoot = path === "/";
  const title = isRoot ? "Workspace" : basename(path);
  const isFav = (favorites.data ?? []).some((f) => f.path === path);
  const canFavorite = !isRoot && path !== "/Users";

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    const now = Date.now();
    return (list.data ?? []).filter((o) => {
      if (term && !basename(o.path).toLowerCase().includes(term)) return false;
      if (type !== "all" && o.object_type !== type) return false;
      if (modified !== "any") {
        const age = now - (o.modified_at ?? 0);
        if (modified === "older" ? age < MODIFIED_WINDOWS["30d"] : age > MODIFIED_WINDOWS[modified]) return false;
      }
      return true;
    });
  }, [list.data, q, type, modified]);

  const upload = () =>
    pickFiles(async (files) => {
      const n = await uploadFiles(path, files);
      invalidate(path);
      if (n) toast.success(`Uploaded ${n} file${n === 1 ? "" : "s"}`);
    });

  return (
    <>
      <Breadcrumb path={path} />
      <div className="page-header">
        <h1 className="page-title">
          <span className="title-text">{title}</span>
          {canFavorite && (
            <button
              className={`icon-btn star-btn${isFav ? " on" : ""}`}
              title={isFav ? "Remove from favorites" : "Add to favorites"}
              onClick={() => toggleFav.mutate({ path, favorite: !isFav })}
            >
              <Star size={18} fill={isFav ? "currentColor" : "none"} />
            </button>
          )}
        </h1>
        <div className="page-actions">
          <Menu
            trigger={({ onClick }) => (
              <button className="btn btn-primary" onClick={onClick} disabled={isRoot}>
                Create
                <ChevronDown size={14} />
              </button>
            )}
            items={[
              { label: "Folder", icon: <FolderPlus size={16} />, onClick: () => setCreating("folder") },
              { label: "File", icon: <FileCode size={16} />, onClick: () => setCreating("file") },
              { label: "Notebook", icon: <NotebookText size={16} />, onClick: () => setCreating("notebook") },
              { separator: true, label: "" },
              { label: "Upload files", icon: <Upload size={16} />, onClick: upload },
            ]}
          />
        </div>
      </div>

      <div className="toolbar">
        <div className="search-input" style={{ width: 260 }}>
          <Search size={14} />
          <input className="input" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="select pill" value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
          <option value="all">Type</option>
          <option value="DIRECTORY">Folder</option>
          <option value="NOTEBOOK">Notebook</option>
          <option value="FILE">File</option>
        </select>
        <select className="select pill" value={modified} onChange={(e) => setModified(e.target.value)} aria-label="Last modified">
          <option value="any">Last modified</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
          <option value="older">Older than 30 days</option>
        </select>
      </div>

      {list.data ? (
        filtered.length > 0 ? (
          <ObjectTable objects={filtered} />
        ) : list.data.length > 0 ? (
          <EmptyState compact title="No matching items" description="Try a different search or filter." />
        ) : (
          <EmptyState
            title="This folder is empty"
            description={isRoot ? undefined : "Create a notebook, file or folder, or upload files here."}
            action={
              !isRoot && (
                <button className="btn btn-primary" onClick={() => setCreating("notebook")}>
                  Create notebook
                </button>
              )
            }
          />
        )
      ) : (
        <QueryState query={list} what="The workspace" />
      )}

      {creating && <CreateObjectDialog kind={creating} parent={path} onClose={() => setCreating(null)} />}
    </>
  );
}

// --- favorites -----------------------------------------------------------------------------

function FavoritesView() {
  const favorites = useFavorites();
  return (
    <>
      <div className="breadcrumb">
        <Link to="/browse">Workspace</Link>
        <ChevronRight size={14} />
      </div>
      <div className="page-header">
        <h1 className="page-title">Favorites</h1>
      </div>
      {favorites.data ? (
        favorites.data.length ? (
          <ObjectTable objects={favorites.data} showLocation />
        ) : (
          <EmptyState
            icon={<Star size={22} />}
            title="No favorites yet"
            description="Star a folder, notebook or file to find it here."
          />
        )
      ) : (
        <QueryState query={favorites} what="Favorites" />
      )}
    </>
  );
}

// --- trash ---------------------------------------------------------------------------------

function TrashView() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const invalidate = useInvalidateWorkspace();
  const trash = useQuery({
    queryKey: keys.wsTrash,
    queryFn: () => get<{ items?: TrashItem[] }>("/api/duckbricks/workspace/trash").then((r) => r.items ?? []),
  });
  const [confirm, setConfirm] = useState<TrashItem | "all" | null>(null);

  const restore = useMutation({
    mutationFn: (item: TrashItem) =>
      post<{ path?: string }>("/api/duckbricks/workspace/trash/restore", { trash_id: item.trash_id }),
    onSuccess: (res, item) => {
      qc.invalidateQueries({ queryKey: keys.wsTrash });
      invalidate();
      const restored = res.path ?? item.path;
      toast.success(`Restored ${basename(restored)}`, { label: "Show", onClick: () => navigate(browseUrl(dirname(restored))) });
    },
  });
  const purge = useMutation({
    mutationFn: (item: TrashItem | "all") =>
      post("/api/duckbricks/workspace/trash/purge", item === "all" ? {} : { trash_id: item.trash_id }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.wsTrash });
      setConfirm(null);
    },
  });

  const items = [...(trash.data ?? [])].sort((a, b) => (b.deleted_at ?? 0) - (a.deleted_at ?? 0));

  return (
    <>
      <div className="breadcrumb">
        <Link to="/browse">Workspace</Link>
        <ChevronRight size={14} />
      </div>
      <div className="page-header">
        <h1 className="page-title">Trash</h1>
        <div className="page-actions">
          <button className="btn" disabled={!items.length} onClick={() => setConfirm("all")}>
            <Trash size={14} />
            Empty trash
          </button>
        </div>
      </div>
      <p className="secondary" style={{ marginTop: -6 }}>
        Deleted items stay here until you restore them or delete them permanently.
      </p>
      {trash.data ? (
        items.length ? (
          <div className="table-wrap">
            <table className="dt">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Original location</th>
                  <th>Deleted</th>
                  <th className="actions-cell" />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.trash_id}>
                    <td>
                      <div className="name-cell">
                        <ObjectIcon type={item.object_type} path={item.path} />
                        <span className="truncate">{basename(item.path)}</span>
                      </div>
                    </td>
                    <td className="secondary">{objectTypeLabel(item)}</td>
                    <td className="secondary mono">{dirname(item.path)}</td>
                    <td className="secondary">
                      <RelTime value={item.deleted_at} />
                    </td>
                    <td className="actions-cell">
                      <div className="row-actions visible">
                        <button className="btn btn-sm" onClick={() => restore.mutate(item)} disabled={restore.isPending}>
                          <RotateCcw size={13} />
                          Restore
                        </button>
                        <button className="icon-btn" title="Delete permanently" onClick={() => setConfirm(item)}>
                          <Trash size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Trash size={22} />} title="Trash is empty" />
        )
      ) : (
        <QueryState query={trash} what="Trash" />
      )}
      {confirm && (
        <ConfirmDialog
          title={confirm === "all" ? "Empty trash?" : "Delete permanently?"}
          message={
            confirm === "all"
              ? `All ${items.length} item${items.length === 1 ? "" : "s"} in Trash will be deleted permanently. This cannot be undone.`
              : `${basename(confirm.path)} will be deleted permanently. This cannot be undone.`
          }
          confirmLabel="Delete permanently"
          danger
          busy={purge.isPending}
          onClose={() => setConfirm(null)}
          onConfirm={() => purge.mutate(confirm)}
        />
      )}
    </>
  );
}

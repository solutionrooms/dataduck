// Top-bar search: recents, workspace objects, tables and jobs, grouped, keyboard navigable.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { Search, Workflow } from "lucide-react";
import { useHomePath, useJobs, useStatus } from "../api/hooks";
import { useTableIndex, useWorkspaceIndex } from "../api/search";
import { addRecent, recentKindLabel, useRecents } from "../lib/recents";
import { basename, browseUrl, dirname, editorUrl, notebookUrl } from "../lib/paths";
import { ObjectIcon, RecentIcon, TableIcon } from "./Icons";
import { Spinner } from "./States";

interface Result {
  id: string;
  icon: ReactNode;
  title: string;
  sub?: string;
  go: () => void;
}

interface Group {
  title: string;
  items: Result[];
  loading?: boolean;
}

const PER_GROUP = 6;

export function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const home = useHomePath();
  const { data: status } = useStatus();
  const recents = useRecents();

  const wsIndex = useWorkspaceIndex(home, everOpened);
  const tableIndex = useTableIndex(everOpened);
  const jobs = useJobs();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "p")) {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
        setEverOpened(true);
      }
    };
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, []);

  const groups = useMemo<Group[]>(() => {
    const term = q.trim().toLowerCase();
    const match = (s: string) => s.toLowerCase().includes(term);
    const close = () => {
      setOpen(false);
      setQ("");
      inputRef.current?.blur();
    };
    const out: Group[] = [];

    const recentItems = recents
      .filter((r) => !term || match(r.title) || match(r.subtitle ?? ""))
      .slice(0, term ? PER_GROUP : 8)
      .map<Result>((r) => ({
        id: `r:${r.kind}:${r.url}`,
        icon: <RecentIcon kind={r.kind} />,
        title: r.title,
        sub: r.subtitle ?? recentKindLabel[r.kind],
        go: () => {
          close();
          if (r.href) window.open(r.href, "_blank", "noopener");
          else navigate(r.url);
        },
      }));
    if (recentItems.length) out.push({ title: "Recents", items: recentItems });
    if (!term) return out;

    const ws = (wsIndex.data ?? [])
      .filter((o) => match(basename(o.path)))
      .slice(0, PER_GROUP)
      .map<Result>((o) => ({
        id: `w:${o.path}`,
        icon: <ObjectIcon type={o.object_type} path={o.path} />,
        title: basename(o.path),
        sub: dirname(o.path),
        go: () => {
          close();
          if (o.object_type === "DIRECTORY") navigate(browseUrl(o.path));
          else if (o.object_type === "NOTEBOOK") {
            const href = notebookUrl(status?.links.notebooks, o.path);
            addRecent({ kind: "notebook", title: basename(o.path), url: editorUrl(o.path), href, subtitle: dirname(o.path) });
            window.open(href, "_blank", "noopener");
          } else navigate(editorUrl(o.path));
        },
      }));
    out.push({ title: "Workspace", items: ws, loading: wsIndex.isFetching && !wsIndex.data });

    const tables = (tableIndex.data ?? [])
      .filter((t) => match(t.name) || match(t.full_name))
      .slice(0, PER_GROUP)
      .map<Result>((t) => ({
        id: `t:${t.full_name}`,
        icon: <TableIcon tableType={t.table_type} />,
        title: t.name,
        sub: t.full_name,
        go: () => {
          close();
          navigate(`/explore/data/${t.full_name.split(".").map(encodeURIComponent).join("/")}`);
        },
      }));
    out.push({ title: "Tables", items: tables, loading: tableIndex.isFetching && !tableIndex.data });

    const jobItems = (jobs.data ?? [])
      .filter((j) => match(j.settings.name ?? "") || String(j.job_id) === term)
      .slice(0, PER_GROUP)
      .map<Result>((j) => ({
        id: `j:${j.job_id}`,
        icon: <Workflow size={16} className="icon-job" />,
        title: j.settings.name ?? `Job ${j.job_id}`,
        sub: `Job ${j.job_id}`,
        go: () => {
          close();
          navigate(`/jobs/${j.job_id}`);
        },
      }));
    out.push({ title: "Jobs", items: jobItems, loading: jobs.isFetching && !jobs.data });
    return out;
  }, [q, recents, wsIndex.data, wsIndex.isFetching, tableIndex.data, tableIndex.isFetching, jobs.data, jobs.isFetching, navigate, status]);

  const flat = groups.flatMap((g) => g.items);
  useEffect(() => setActive(0), [q]);

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(flat.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      flat[active]?.go();
    } else if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  };

  let idx = -1;
  const visible = groups.filter((g) => g.items.length || g.loading);
  return (
    <div className="global-search" ref={boxRef}>
      <div className="global-search-box">
        <Search size={15} />
        <input
          ref={inputRef}
          value={q}
          placeholder="Search data, notebooks, recents, and more..."
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => {
            setOpen(true);
            setEverOpened(true);
          }}
          onKeyDown={onKeyDown}
          aria-label="Search"
        />
        <span className="kbd">⌘ P</span>
      </div>
      {open && (visible.length > 0 || q.trim()) && (
        <div className="search-results" role="listbox">
          {visible.map((g) => (
            <div key={g.title}>
              <div className="search-group-title">{g.title}</div>
              {g.loading && g.items.length === 0 && (
                <div className="search-item muted">
                  <Spinner size={14} /> Searching…
                </div>
              )}
              {g.items.map((item) => {
                idx++;
                const i = idx;
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`search-item${i === active ? " active" : ""}`}
                    onMouseEnter={() => setActive(i)}
                    onClick={item.go}
                    role="option"
                    aria-selected={i === active}
                  >
                    {item.icon}
                    <span className="truncate">{item.title}</span>
                    {item.sub && <span className="sub truncate">{item.sub}</span>}
                  </button>
                );
              })}
            </div>
          ))}
          {q.trim() && flat.length === 0 && !visible.some((g) => g.loading) && (
            <div className="search-empty">No results for “{q.trim()}”</div>
          )}
        </div>
      )}
    </div>
  );
}

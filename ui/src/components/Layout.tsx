import { useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import {
  BookOpen,
  Clock,
  Cpu,
  Database,
  FileCode,
  Folder,
  FolderPlus,
  LayoutGrid,
  LogOut,
  NotebookText,
  Play,
  Plus,
  ScrollText,
  SquareTerminal,
  Upload,
  Warehouse,
  Workflow,
} from "lucide-react";
import { clearToken } from "../api/client";
import { useHomePath, useStatus } from "../api/hooks";
import { browseUrl } from "../lib/paths";
import { openSqlTab } from "../lib/sqlTabs";
import { toast } from "../lib/toast";
import { GlobalSearch } from "./GlobalSearch";
import { DuckMark } from "./Icons";
import { Menu } from "./Menu";
import { Toasts } from "./Toasts";
import { CreateObjectDialog, pickFiles, uploadFiles, useInvalidateWorkspace, type CreateKind } from "./WorkspaceDialogs";

function NavItem({ to, icon, label, match }: { to: string; icon: ReactNode; label: string; match?: string[] }) {
  const { pathname } = useLocation();
  const prefixes = match ?? [to];
  const active = prefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  return (
    <NavLink to={to} className={`nav-item${active ? " active" : ""}`}>
      {icon}
      <span className="truncate">{label}</span>
    </NavLink>
  );
}

function NewButton() {
  const home = useHomePath();
  const navigate = useNavigate();
  const invalidate = useInvalidateWorkspace();
  const [creating, setCreating] = useState<CreateKind | null>(null);

  return (
    <>
      <Menu
        align="left"
        trigger={({ onClick }) => (
          <button className="new-btn" onClick={onClick}>
            <span className="plus">
              <Plus size={14} strokeWidth={3} />
            </span>
            New
          </button>
        )}
        items={[
          { label: "Notebook", icon: <NotebookText size={16} />, onClick: () => setCreating("notebook"), disabled: !home },
          {
            label: "Query",
            icon: <SquareTerminal size={16} />,
            onClick: () => {
              openSqlTab();
              navigate("/sql/editor");
            },
          },
          { label: "Job", icon: <Workflow size={16} />, onClick: () => navigate("/jobs?create=1") },
          { separator: true, label: "" },
          { label: "File", icon: <FileCode size={16} />, onClick: () => setCreating("file"), disabled: !home },
          { label: "Folder", icon: <FolderPlus size={16} />, onClick: () => setCreating("folder"), disabled: !home },
          {
            label: "Upload files",
            icon: <Upload size={16} />,
            disabled: !home,
            onClick: () =>
              home &&
              pickFiles(async (files) => {
                const n = await uploadFiles(home, files);
                invalidate(home);
                if (n) toast.success(`Uploaded ${n} file${n === 1 ? "" : "s"} to your home folder`);
              }),
          },
        ]}
      />
      {creating && home && <CreateObjectDialog kind={creating} parent={home} onClose={() => setCreating(null)} />}
    </>
  );
}

function Sidebar() {
  const home = useHomePath();
  const { data: status } = useStatus();
  return (
    <nav className="sidebar" aria-label="Main">
      <NewButton />
      <NavItem
        to={home ? browseUrl(home) : "/browse"}
        icon={<Folder size={16} />}
        label="Workspace"
        match={["/browse", "/editor"]}
      />
      <NavItem to="/recents" icon={<Clock size={16} />} label="Recents" />
      <NavItem to="/explore/data" icon={<Database size={16} />} label="Catalog" />
      <NavItem to="/jobs" icon={<Workflow size={16} />} label="Jobs & Pipelines" match={["/jobs", "/pipelines"]} />
      <NavItem to="/compute" icon={<Cpu size={16} />} label="Compute" />

      <div className="nav-section">SQL</div>
      <NavItem to="/sql/editor" icon={<SquareTerminal size={16} />} label="SQL Editor" />
      <NavItem to="/sql/history" icon={<ScrollText size={16} />} label="Query History" />
      <NavItem to="/sql/warehouses" icon={<Warehouse size={16} />} label="SQL Warehouses" />

      <div className="nav-section">Data Engineering</div>
      <NavItem to="/runs" icon={<Play size={16} />} label="Runs" />
      <a className="nav-item" href={status?.links.notebooks ?? "http://127.0.0.1:2718"} target="_blank" rel="noreferrer">
        <BookOpen size={16} />
        <span className="truncate">Notebooks</span>
      </a>

      <div className="sidebar-footer">
        {status ? `DuckDB ${status.versions.duckdb}` : " "}
      </div>
    </nav>
  );
}

function UserMenu() {
  const { data: status } = useStatus();
  const navigate = useNavigate();
  const user = status?.user ?? "";
  return (
    <Menu
      trigger={({ onClick }) => (
        <button className="avatar" onClick={onClick} title={user} aria-label="Account">
          {user.charAt(0) || "?"}
        </button>
      )}
      items={[
        { heading: true, label: user || "Signed in" },
        { label: "Home", icon: <LayoutGrid size={16} />, to: "/" },
        { separator: true, label: "" },
        {
          label: "Sign out",
          icon: <LogOut size={16} />,
          onClick: () => {
            clearToken();
            navigate("/login");
          },
        },
      ]}
    />
  );
}

export function Layout() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          <DuckMark />
          Duckbricks
        </Link>
        <div className="topbar-search">
          <GlobalSearch />
        </div>
        <div className="topbar-right">
          <UserMenu />
        </div>
      </header>
      <Sidebar />
      <main className="main">
        <Outlet />
      </main>
      <Toasts />
    </div>
  );
}

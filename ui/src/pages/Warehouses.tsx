// SQL warehouses: list, start/stop, create, delete.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { EllipsisVertical, Play, Plus, Square, Trash, Warehouse as WarehouseIcon } from "lucide-react";
import { del, isUnavailable, post } from "../api/client";
import { keys, useNodeTypes, useWarehouses } from "../api/hooks";
import type { Warehouse } from "../api/types";
import { ConfirmDialog, Dialog, DialogButtons } from "../components/Dialog";
import { Menu } from "../components/Menu";
import { EmptyState, QueryState } from "../components/States";
import { titleCase } from "../lib/runState";

const SIZES = ["2X-Small", "X-Small", "Small", "Medium", "Large"];

function stateDot(state: string) {
  const cls = state === "RUNNING" ? "green" : state === "STARTING" || state === "STOPPING" ? "blue pulse" : state === "DELETING" ? "red" : "";
  return <span className={`dot ${cls}`} />;
}

export function WarehousesPage() {
  const warehouses = useWarehouses();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Warehouse | null>(null);

  const action = useMutation({
    mutationFn: ({ id, op }: { id: string; op: "start" | "stop" }) => post(`/api/2.0/sql/warehouses/${id}/${op}`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.warehouses }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/2.0/sql/warehouses/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.warehouses });
      setDeleting(null);
    },
  });
  const unavailable = warehouses.isError && isUnavailable(warehouses.error);

  return (
    <div className="panel">
      <div className="page">
        <div className="page-header">
          <h1 className="page-title">SQL warehouses</h1>
          <div className="page-actions">
            <button className="btn btn-primary" onClick={() => setCreating(true)} disabled={unavailable}>
              <Plus size={14} />
              Create SQL warehouse
            </button>
          </div>
        </div>
        <p className="secondary" style={{ marginTop: -6, marginBottom: 16 }}>
          A warehouse is a long-running DuckDB server reachable over the Quack protocol. Without one, SQL runs serverless
          inside the API process.
        </p>
        {warehouses.data ? (
          warehouses.data.length ? (
            <table className="dt">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>State</th>
                  <th>Size</th>
                  <th>Auto stop</th>
                  <th>Connection</th>
                  <th className="actions-cell" />
                </tr>
              </thead>
              <tbody>
                {warehouses.data.map((w) => {
                  const busy = action.isPending && action.variables?.id === w.id;
                  const running = w.state === "RUNNING" || w.state === "STARTING";
                  return (
                    <tr key={w.id}>
                      <td>
                        <div className="name-cell">
                          {stateDot(w.state)}
                          <span style={{ fontWeight: 600 }}>{w.name}</span>
                          <span className="muted mono" style={{ fontSize: 11 }}>
                            {w.id}
                          </span>
                        </div>
                      </td>
                      <td className="secondary">{titleCase(w.state)}</td>
                      <td className="secondary">{w.cluster_size ?? "–"}</td>
                      <td className="secondary">{w.auto_stop_mins ? `${w.auto_stop_mins} min` : "Never"}</td>
                      <td className="secondary mono" style={{ fontSize: 12 }}>
                        {w.state === "RUNNING" && w.odbc_params?.hostname && w.odbc_params.port
                          ? `${w.odbc_params.protocol ? `${w.odbc_params.protocol}://` : ""}${w.odbc_params.hostname}:${w.odbc_params.port}`
                          : "–"}
                      </td>
                      <td className="actions-cell">
                        <div className="row-actions visible">
                          {running ? (
                            <button className="btn btn-sm" disabled={busy || w.state === "STOPPING"} onClick={() => action.mutate({ id: w.id, op: "stop" })}>
                              <Square size={11} fill="currentColor" />
                              Stop
                            </button>
                          ) : (
                            <button className="btn btn-sm" disabled={busy || w.state === "STOPPING"} onClick={() => action.mutate({ id: w.id, op: "start" })}>
                              <Play size={11} fill="currentColor" />
                              Start
                            </button>
                          )}
                          <Menu
                            trigger={({ onClick }) => (
                              <button className="icon-btn" onClick={onClick} aria-label="More actions">
                                <EllipsisVertical size={16} />
                              </button>
                            )}
                            items={[{ label: "Delete", icon: <Trash size={16} />, danger: true, onClick: () => setDeleting(w) }]}
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <EmptyState
              icon={<WarehouseIcon size={22} />}
              title="No SQL warehouses"
              description="Queries run serverless (in-process) until you create a warehouse."
              action={
                <button className="btn btn-primary" onClick={() => setCreating(true)}>
                  Create SQL warehouse
                </button>
              }
            />
          )
        ) : (
          <QueryState query={warehouses} what="SQL warehouses" />
        )}
      </div>
      {creating && <CreateWarehouseDialog onClose={() => setCreating(false)} />}
      {deleting && (
        <ConfirmDialog
          title="Delete SQL warehouse?"
          message={`${deleting.name} will be stopped and deleted.`}
          confirmLabel="Delete"
          danger
          busy={remove.isPending}
          onClose={() => setDeleting(null)}
          onConfirm={() => remove.mutate(deleting.id)}
        />
      )}
    </div>
  );
}

function CreateWarehouseDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [size, setSize] = useState("Small");
  const [autoStop, setAutoStop] = useState("10");
  const nodeTypes = useNodeTypes();
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      post<{ id: string }>("/api/2.0/sql/warehouses", {
        name: name.trim(),
        cluster_size: size.trim(),
        auto_stop_mins: Number(autoStop) || 0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.warehouses });
      onClose();
    },
  });
  return (
    <Dialog
      title="New SQL warehouse"
      onClose={onClose}
      onSubmit={() => name.trim() && create.mutate()}
      footer={<DialogButtons onCancel={onClose} confirmLabel="Create" busy={create.isPending} disabled={!name.trim()} />}
    >
      <div className="field">
        <label htmlFor="wh-name">Name</label>
        <input id="wh-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="wh-size">Cluster size</label>
        <input id="wh-size" className="input" list="wh-sizes" value={size} onChange={(e) => setSize(e.target.value)} />
        <datalist id="wh-sizes">
          {SIZES.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <span className="hint">
          Sizes map to node types
          {nodeTypes.data?.length ? `: ${nodeTypes.data.map((n) => `${n.node_type_id} (${n.memory_limit ?? "?"}, ${n.num_cores ?? "?"} threads)`).join(", ")}` : ""}.
        </span>
      </div>
      <div className="field">
        <label htmlFor="wh-stop">Auto stop (minutes, 0 = never)</label>
        <input id="wh-stop" className="input" type="number" min={0} value={autoStop} onChange={(e) => setAutoStop(e.target.value)} />
      </div>
    </Dialog>
  );
}

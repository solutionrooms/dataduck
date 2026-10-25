// Create folder / file / notebook dialogs and upload, shared by the Workspace page and "+ New".

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { bytesToBase64, post, textToBase64 } from "../api/client";
import { keys, useStatus } from "../api/hooks";
import { MARIMO_TEMPLATE, browseUrl, editorUrl, joinPath, notebookUrl } from "../lib/paths";
import { toast } from "../lib/toast";
import { Dialog, DialogButtons } from "./Dialog";

export type CreateKind = "folder" | "file" | "notebook";

const DEFAULT_NAME: Record<CreateKind, string> = {
  folder: "New Folder",
  file: "untitled.sql",
  notebook: "Untitled Notebook.py",
};

const TITLE: Record<CreateKind, string> = {
  folder: "Create folder",
  file: "Create file",
  notebook: "Create notebook",
};

export function useInvalidateWorkspace() {
  const qc = useQueryClient();
  return (dir?: string) => {
    if (dir) qc.invalidateQueries({ queryKey: keys.wsList(dir) });
    else qc.invalidateQueries({ queryKey: ["ws-list"] });
    qc.invalidateQueries({ queryKey: ["search-index", "workspace"] });
  };
}

export function CreateObjectDialog({
  kind,
  parent,
  onClose,
}: {
  kind: CreateKind;
  parent: string;
  onClose: () => void;
}) {
  const [name, setName] = useState(DEFAULT_NAME[kind]);
  const navigate = useNavigate();
  const invalidate = useInvalidateWorkspace();
  const { data: status } = useStatus();

  const create = useMutation({
    mutationFn: async () => {
      let clean = name.trim().replace(/^\/+|\/+$/g, "");
      if (kind === "notebook" && !clean.endsWith(".py")) clean += ".py";
      const path = joinPath(parent, clean);
      if (kind === "folder") await post("/api/2.0/workspace/mkdirs", { path });
      else {
        await post("/api/2.0/workspace/import", {
          path,
          content: textToBase64(kind === "notebook" ? MARIMO_TEMPLATE : ""),
          format: "AUTO",
          overwrite: false,
        });
      }
      return path;
    },
    onSuccess: (path) => {
      invalidate(parent);
      onClose();
      if (kind === "folder") navigate(browseUrl(path));
      else if (kind === "file") navigate(editorUrl(path));
      else {
        navigate(browseUrl(parent));
        toast.success(`Created ${clean(path)}`, { label: "Open in marimo", href: notebookUrl(status?.links.notebooks, path) });
      }
    },
  });

  return (
    <Dialog
      title={TITLE[kind]}
      onClose={onClose}
      onSubmit={() => name.trim() && create.mutate()}
      footer={<DialogButtons onCancel={onClose} confirmLabel="Create" busy={create.isPending} disabled={!name.trim()} />}
    >
      <div className="field">
        <label htmlFor="create-name">Name</label>
        <input
          id="create-name"
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onFocus={(e) => {
            const dot = e.target.value.lastIndexOf(".");
            e.target.setSelectionRange(0, dot > 0 && kind !== "folder" ? dot : e.target.value.length);
          }}
        />
        {kind === "file" && <span className="hint">The extension picks the editor language: .sql, .py, .yml, .json, .md</span>}
        {kind === "notebook" && <span className="hint">A marimo notebook with the Duckbricks connection cell.</span>}
      </div>
      <div className="field">
        <span className="field-label">Location</span>
        <span className="mono secondary">{parent}</span>
      </div>
    </Dialog>
  );
}

function clean(path: string) {
  return path.slice(path.lastIndexOf("/") + 1);
}

async function readAsBase64(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  return bytesToBase64(new Uint8Array(buf));
}

/** Upload browser files into a workspace folder (import, no overwrite). */
export async function uploadFiles(parent: string, files: FileList | File[]): Promise<number> {
  let ok = 0;
  for (const file of Array.from(files)) {
    try {
      await post("/api/2.0/workspace/import", {
        path: joinPath(parent, file.name),
        content: await readAsBase64(file),
        format: "AUTO",
        overwrite: false,
      });
      ok++;
    } catch (err) {
      toast.error(err);
    }
  }
  return ok;
}

/** Hidden file input + trigger for uploads. */
export function pickFiles(onPicked: (files: FileList) => void): void {
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.onchange = () => {
    if (input.files && input.files.length) onPicked(input.files);
  };
  input.click();
}

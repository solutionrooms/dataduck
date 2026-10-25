// Lazy entry point for the CodeMirror editor so CodeMirror ships in its own chunk.

import { lazy, Suspense } from "react";
import type { CodeEditorProps } from "./CodeEditor";
import { Loading } from "./States";

const CodeEditorImpl = lazy(() => import("./CodeEditor"));

export type { CodeEditorHandle } from "./CodeEditor";

export function Editor(props: CodeEditorProps) {
  return (
    <Suspense fallback={<Loading compact label="Loading editor…" />}>
      <CodeEditorImpl {...props} />
    </Suspense>
  );
}

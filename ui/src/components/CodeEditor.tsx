// CodeMirror 6 wrapper: language by name, dark theme, Mod-Enter / Mod-S hooks, imperative handle.

import { useEffect, useImperativeHandle, useRef, type Ref } from "react";
import { basicSetup } from "codemirror";
import { Compartment, EditorState, Prec, type Extension } from "@codemirror/state";
import { EditorView, keymap, placeholder as placeholderExt } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { indentWithTab } from "@codemirror/commands";
import { closeCompletion } from "@codemirror/autocomplete";
import { tags as t } from "@lezer/highlight";
import { PostgreSQL, sql, type SQLNamespace } from "@codemirror/lang-sql";
import { python } from "@codemirror/lang-python";
import { yaml } from "@codemirror/lang-yaml";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import type { EditorLanguage } from "../lib/paths";

export interface CodeEditorHandle {
  /** The selected text, or the whole document when nothing is selected. */
  getSelectionOrAll: () => string;
  insertText: (text: string) => void;
  focus: () => void;
}

export interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  language?: EditorLanguage;
  readOnly?: boolean;
  placeholder?: string;
  onRun?: () => void;
  onSave?: () => void;
  sqlSchema?: SQLNamespace;
  sqlDefaultSchema?: string;
  ref?: Ref<CodeEditorHandle>;
}

const darkTheme = EditorView.theme(
  {
    "&": { height: "100%", backgroundColor: "#1b1f23", color: "#dfe3e8", fontSize: "13px" },
    ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55", overflow: "auto" },
    ".cm-content": { caretColor: "#e6e8eb", padding: "8px 0" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "#e6e8eb" },
    "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
      { backgroundColor: "rgba(74, 163, 255, 0.28)" },
    ".cm-gutters": { backgroundColor: "#1b1f23", color: "#5f6872", border: "none", paddingLeft: "4px" },
    ".cm-activeLineGutter": { backgroundColor: "transparent", color: "#aeb5bd" },
    ".cm-activeLine": { backgroundColor: "rgba(255, 255, 255, 0.035)" },
    ".cm-foldPlaceholder": { backgroundColor: "#2a2f35", border: "none", color: "#aeb5bd" },
    ".cm-matchingBracket, .cm-nonmatchingBracket": { backgroundColor: "rgba(74, 163, 255, 0.22)", outline: "none" },
    ".cm-searchMatch": { backgroundColor: "rgba(245, 196, 67, 0.25)" },
    ".cm-tooltip": { backgroundColor: "#2a2f35", border: "1px solid #41474e", color: "#e6e8eb" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "#2b7de9", color: "#fff" },
    ".cm-panels": { backgroundColor: "#22262b", color: "#e6e8eb" },
    ".cm-panels.cm-panels-top": { borderBottom: "1px solid #2f3337" },
    ".cm-textfield": { backgroundColor: "#1d2125", border: "1px solid #454c54", color: "#e6e8eb" },
    ".cm-button": { backgroundImage: "none", backgroundColor: "#2a2f35", border: "1px solid #454c54", color: "#e6e8eb" },
    ".cm-placeholder": { color: "#6b747d" },
  },
  { dark: true },
);

const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword, t.modifier], color: "#6cb6ff" },
  { tag: [t.controlKeyword, t.definitionKeyword], color: "#c792ea" },
  { tag: [t.string, t.special(t.string), t.regexp], color: "#e3a978" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "#a5d6a7" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "#6b8f71", fontStyle: "italic" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "#f0d58c" },
  { tag: [t.definition(t.variableName), t.className], color: "#7fd6c2" },
  { tag: [t.typeName, t.standard(t.name)], color: "#4ec9b0" },
  { tag: [t.propertyName, t.attributeName], color: "#9cdcfe" },
  { tag: [t.heading], color: "#6cb6ff", fontWeight: "bold" },
  { tag: [t.punctuation, t.bracket, t.operator], color: "#b4bcc5" },
  { tag: t.invalid, color: "#f0605d" },
]);

function languageExtension(lang: EditorLanguage, schema?: SQLNamespace, defaultSchema?: string): Extension {
  switch (lang) {
    case "sql":
      return sql({ dialect: PostgreSQL, schema, defaultSchema, upperCaseKeywords: false });
    case "python":
      return python();
    case "yaml":
      return yaml();
    case "json":
      return json();
    case "markdown":
      return markdown();
    default:
      return [];
  }
}

export default function CodeEditor({
  value,
  onChange,
  language = "text",
  readOnly = false,
  placeholder,
  onRun,
  onSave,
  sqlSchema,
  sqlDefaultSchema,
  ref,
}: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const langComp = useRef(new Compartment());
  const roComp = useRef(new Compartment());
  const callbacks = useRef({ onChange, onRun, onSave });
  callbacks.current = { onChange, onRun, onSave };

  useEffect(() => {
    if (!host.current) return;
    const keys = Prec.highest(
      keymap.of([
        {
          key: "Mod-Enter",
          run: (v) => {
            if (!callbacks.current.onRun) return false;
            closeCompletion(v);
            callbacks.current.onRun();
            return true;
          },
        },
        {
          key: "Shift-Enter",
          run: (v) => {
            if (!callbacks.current.onRun) return false;
            closeCompletion(v);
            callbacks.current.onRun();
            return true;
          },
        },
        {
          key: "Mod-s",
          preventDefault: true,
          run: () => {
            callbacks.current.onSave?.();
            return true;
          },
        },
      ]),
    );
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          keys,
          basicSetup,
          keymap.of([indentWithTab]),
          darkTheme,
          syntaxHighlighting(highlight),
          langComp.current.of(languageExtension(language, sqlSchema, sqlDefaultSchema)),
          roComp.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
          placeholder ? placeholderExt(placeholder) : [],
          EditorView.updateListener.of((u) => {
            if (u.docChanged) callbacks.current.onChange?.(u.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // The editor is created once; prop changes are applied through compartments below.
  }, []);

  useEffect(() => {
    view.current?.dispatch({
      effects: langComp.current.reconfigure(languageExtension(language, sqlSchema, sqlDefaultSchema)),
    });
  }, [language, sqlSchema, sqlDefaultSchema]);

  useEffect(() => {
    view.current?.dispatch({
      effects: roComp.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    });
  }, [readOnly]);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const current = v.state.doc.toString();
    if (current !== value) {
      v.dispatch({ changes: { from: 0, to: current.length, insert: value } });
    }
  }, [value]);

  useImperativeHandle(
    ref,
    () => ({
      getSelectionOrAll: () => {
        const v = view.current;
        if (!v) return value;
        const sel = v.state.selection.main;
        return sel.empty ? v.state.doc.toString() : v.state.sliceDoc(sel.from, sel.to);
      },
      insertText: (text: string) => {
        const v = view.current;
        if (!v) return;
        v.dispatch(v.state.replaceSelection(text));
        v.focus();
      },
      focus: () => view.current?.focus(),
    }),
    [value],
  );

  return <div className="cm-host" ref={host} />;
}

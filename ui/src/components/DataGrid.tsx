// Results grid with row windowing, so 10k-row results stay responsive.

import { useMemo, useRef, useState, type UIEvent } from "react";

const ROW_H = 28;
const OVERSCAN = 20;

const NUMERIC = /^(TINYINT|SMALLINT|INT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|REAL|DOUBLE|DECIMAL|NUMERIC|LONG|SHORT|BYTE)/i;

export interface GridColumn {
  name: string;
  type_text?: string;
}

export function DataGrid({
  columns,
  rows,
  height,
}: {
  columns: GridColumn[];
  rows: (string | null)[][];
  height?: number | string;
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(600);
  const ref = useRef<HTMLDivElement>(null);
  const numeric = useMemo(() => columns.map((c) => NUMERIC.test(c.type_text ?? "")), [columns]);

  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    setScrollTop(e.currentTarget.scrollTop);
    setViewport(e.currentTarget.clientHeight);
  };

  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const end = Math.min(rows.length, Math.ceil((scrollTop + viewport) / ROW_H) + OVERSCAN);
  const topPad = start * ROW_H;
  const bottomPad = (rows.length - end) * ROW_H;

  return (
    <div className="grid" ref={ref} onScroll={onScroll} style={{ height: height ?? "100%" }}>
      <table className="grid-table">
        <thead>
          <tr>
            <th className="rownum">#</th>
            {columns.map((c, i) => (
              <th key={i} className={numeric[i] ? "is-num" : undefined}>
                {c.name}
                {c.type_text && <span className="col-type">{c.type_text.toLowerCase()}</span>}
              </th>
            ))}
            <th className="filler" aria-hidden="true" />
          </tr>
        </thead>
        <tbody>
          {topPad > 0 && (
            <tr style={{ height: topPad }}>
              <td colSpan={columns.length + 2} style={{ padding: 0, border: "none" }} />
            </tr>
          )}
          {rows.slice(start, end).map((row, i) => (
            <tr key={start + i}>
              <td className="rownum">{start + i + 1}</td>
              {columns.map((_, j) => {
                const v = row[j];
                return v === null || v === undefined ? (
                  <td key={j} className="null">
                    null
                  </td>
                ) : (
                  <td key={j} className={numeric[j] ? "is-num" : undefined} title={v.length > 40 ? v : undefined}>
                    {v}
                  </td>
                );
              })}
              <td className="filler" />
            </tr>
          ))}
          {bottomPad > 0 && (
            <tr style={{ height: bottomPad }}>
              <td colSpan={columns.length + 2} style={{ padding: 0, border: "none" }} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function toCsv(columns: GridColumn[], rows: (string | null)[][]): string {
  const esc = (v: string | null) => {
    if (v === null || v === undefined) return "";
    return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  };
  return [columns.map((c) => esc(c.name)).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
}

export function downloadText(filename: string, text: string, mime = "text/csv"): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

"""Parse the Databricks declarative-pipeline SQL subset into datasets DuckDB can build.

Supported:
- `CREATE [OR REFRESH] [PRIVATE|TEMPORARY] MATERIALIZED VIEW name [(constraints)] AS query`
- `CREATE [OR REFRESH] STREAMING TABLE name [(constraints)] AS ... FROM STREAM read_files(...)`
- `CREATE [TEMPORARY] [LIVE] VIEW name AS query`
- `CONSTRAINT c EXPECT (expr) [ON VIOLATION DROP ROW | FAIL UPDATE]`
- `read_files('s3://...', format => 'csv' | 'json' | 'parquet', header => true, ...)`,
  `_metadata.file_path`, `_metadata.file_name`, and the legacy `LIVE.` prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADER = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REFRESH\s+|OR\s+REPLACE\s+)?"
    r"(?P<mods>(?:(?:PRIVATE|TEMPORARY|TEMP|LIVE)\s+)*)"
    r"(?P<kind>MATERIALIZED\s+VIEW|STREAMING\s+(?:LIVE\s+)?TABLE|LIVE\s+TABLE|TABLE|VIEW)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[`\"\w.]+)\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
CONSTRAINT = re.compile(
    r"^\s*CONSTRAINT\s+(?P<name>[`\"\w]+)\s+EXPECT\s*\((?P<expr>.*)\)\s*"
    r"(?:ON\s+VIOLATION\s+(?P<action>DROP\s+ROW|FAIL\s+UPDATE))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
COMMENT = re.compile(r"\bCOMMENT\s+'((?:[^']|'')*)'", re.IGNORECASE)
TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:STREAM\s*\(\s*|STREAM\s+)?(?P<name>(?:LIVE\.)?[`\"\w]+(?:\.[`\"\w]+){0,2})",
    re.IGNORECASE,
)
READ_FILES = re.compile(r"(?P<stream>\bSTREAM\s*\(?\s*)?\bread_files\s*\(", re.IGNORECASE)
STREAM_TABLE = re.compile(
    r"\bSTREAM\s*\(\s*([`\"\w.]+)\s*\)|\bSTREAM\s+(?!read_files)([`\"\w.]+)", re.IGNORECASE
)


class PipelineSqlError(ValueError):
    pass


@dataclass
class Expectation:
    name: str
    expr: str
    action: str  # DROP, FAIL, WARN


@dataclass
class Dataset:
    name: str  # as written, unquoted
    kind: str  # MATERIALIZED_VIEW, STREAMING_TABLE, VIEW
    query: str
    temporary: bool = False
    expectations: list[Expectation] = field(default_factory=list)
    comment: str | None = None
    warnings: list[str] = field(default_factory=list)
    source_file: str = ""


def split_statements(sql: str) -> list[str]:
    """Split on `;` outside quotes and comments; drop comment-only fragments."""
    out, buf = [], []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            end = i + 1
            while end < n and sql[end] != ch:
                end += 2 if sql[end] == "\\" else 1
            buf.append(sql[i : end + 1])
            i = end + 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif ch == ";":
            out.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _balanced(text: str, start: int) -> int:
    """Index of the parenthesis closing the one at `start`."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch in ("'", '"'):
            end = text.find(ch, i + 1)
            i = len(text) if end < 0 else end + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise PipelineSqlError("unbalanced parentheses")


def _split_top_level(text: str) -> list[str]:
    parts, depth, buf, quote = [], 0, [], ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _find_as(rest: str) -> int:
    depth, i, n = 0, 0, len(rest)
    while i < n:
        ch = rest[i]
        if ch in ("'", '"'):
            end = rest.find(ch, i + 1)
            i = n if end < 0 else end + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif (
            depth == 0
            and re.match(r"AS\b", rest[i:], re.IGNORECASE)
            and (i == 0 or not (rest[i - 1].isalnum() or rest[i - 1] == "_"))
        ):
            return i
        i += 1
    return -1


def unquote(name: str) -> str:
    return ".".join(p.strip('`"') for p in name.split("."))


def parse_statement(statement: str, source_file: str = "") -> Dataset | None:
    """A dataset definition, or None for statements to ignore (SET, comments)."""
    if re.match(r"^\s*(SET|USE)\b", statement, re.IGNORECASE):
        return None
    match = HEADER.match(statement)
    if not match:
        raise PipelineSqlError(
            f"{source_file}: unsupported statement (expected CREATE MATERIALIZED VIEW, "
            f"STREAMING TABLE or VIEW): {statement[:120]}"
        )
    mods = match.group("mods").upper().split()
    kind_text = re.sub(r"\s+", " ", match.group("kind").upper())
    if kind_text == "MATERIALIZED VIEW" or kind_text in ("LIVE TABLE", "TABLE"):
        kind = "MATERIALIZED_VIEW"
    elif kind_text.startswith("STREAMING"):
        kind = "STREAMING_TABLE"
    else:
        kind = "VIEW"
    rest = match.group("rest")
    dataset = Dataset(
        name=unquote(match.group("name")),
        kind=kind,
        query="",
        temporary="TEMPORARY" in mods or "TEMP" in mods or "PRIVATE" in mods,
        source_file=source_file,
    )
    if rest.startswith("("):
        close = _balanced(rest, 0)
        for item in _split_top_level(rest[1:close]):
            constraint = CONSTRAINT.match(item)
            if constraint:
                action = (constraint.group("action") or "").upper()
                dataset.expectations.append(
                    Expectation(
                        name=unquote(constraint.group("name")),
                        expr=constraint.group("expr").strip(),
                        action="DROP"
                        if action.startswith("DROP")
                        else "FAIL"
                        if action
                        else "WARN",
                    )
                )
            elif item.upper().startswith("CONSTRAINT"):
                raise PipelineSqlError(f"{dataset.name}: unsupported constraint: {item[:100]}")
            else:
                dataset.warnings.append(f"column definition ignored: {item[:80]}")
        rest = rest[close + 1 :]
    position = _find_as(rest)
    if position < 0:
        raise PipelineSqlError(f"{dataset.name}: missing AS <query>")
    clauses = rest[:position]
    comment = COMMENT.search(clauses)
    if comment:
        dataset.comment = comment.group(1).replace("''", "'")
    for clause in ("TBLPROPERTIES", "PARTITIONED BY", "CLUSTER BY", "LOCATION"):
        if clause in clauses.upper():
            dataset.warnings.append(f"{clause} ignored")
    dataset.query = re.sub(r"\bLIVE\.", "", rest[position + 2 :].strip(), flags=re.IGNORECASE)
    return dataset


def references(query: str) -> set[str]:
    """Table names a query reads (unquoted, as written)."""
    names = {unquote(m.group("name")) for m in TABLE_REF.finditer(query)}
    return {n for n in names if n.lower() not in ("read_files", "stream", "values", "lateral")}


@dataclass
class ReadFiles:
    start: int
    end: int
    path: str
    format: str
    options: dict[str, str]
    stream: bool


def find_read_files(query: str) -> list[ReadFiles]:
    found = []
    for match in READ_FILES.finditer(query):
        open_paren = match.end() - 1
        close = _balanced(query, open_paren)
        args = _split_top_level(query[open_paren + 1 : close])
        if not args:
            raise PipelineSqlError("read_files needs a path")
        path = args[0].strip().strip("'")
        options = {}
        for arg in args[1:]:
            if "=>" not in arg:
                raise PipelineSqlError(f"read_files option must be name => value: {arg}")
            key, value = arg.split("=>", 1)
            options[key.strip().lower()] = value.strip().strip("'")
        end = close + 1
        stream = bool(match.group("stream"))
        if stream and "(" in match.group("stream"):
            rest = query[end:]
            closing = re.match(r"\s*\)", rest)
            if closing:
                end += closing.end()
        fmt = options.pop("format", "").lower() or _guess_format(path)
        found.append(ReadFiles(match.start(), end, path, fmt, options, stream))
    return found


def _guess_format(path: str) -> str:
    for ext in ("csv", "json", "parquet"):
        if path.lower().rstrip("/").endswith(f".{ext}"):
            return ext
    return "csv"


def duckdb_reader(rf: ReadFiles, files: list[str]) -> tuple[str, list[str]]:
    """A DuckDB table function over the given files, plus warnings for ignored options."""
    warnings = []
    listing = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
    args = [listing, "filename = true", "union_by_name = true"]
    if rf.format == "csv":
        reader = "read_csv"
        if "header" in rf.options:
            args.append(f"header = {rf.options['header'].lower()}")
        if "sep" in rf.options or "delimiter" in rf.options:
            sep = rf.options.get("sep") or rf.options.get("delimiter")
            args.append(f"delim = '{sep}'")
    elif rf.format == "json":
        reader = "read_json_auto"
        args.remove("union_by_name = true")
    elif rf.format == "parquet":
        reader = "read_parquet"
    else:
        raise PipelineSqlError(
            f"read_files format {rf.format} is not supported (csv, json, parquet)"
        )
    for key in rf.options:
        if key not in ("header", "sep", "delimiter", "inferschema", "infercolumntypes"):
            warnings.append(f"read_files option {key} ignored")
    return f"{reader}({', '.join(args)})", warnings


def rewrite_metadata_columns(query: str) -> str:
    query = re.sub(r"\b_metadata\.file_path\b", "filename", query, flags=re.IGNORECASE)
    return re.sub(
        r"\b_metadata\.file_name\b", "parse_filename(filename)", query, flags=re.IGNORECASE
    )


def strip_stream(query: str) -> str:
    """`STREAM(t)` / `STREAM t` on a table become plain reads (recomputed, not incremental)."""
    return STREAM_TABLE.sub(lambda m: m.group(1) or m.group(2), query)

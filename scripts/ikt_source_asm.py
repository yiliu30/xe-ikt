#!/usr/bin/env python3
"""Build a source-to-ISA map from IGC shader ASM dumps.

IGC's final .asm listing is annotated with `// File:` and `// Line N:`
comments. This tool groups the following ISA instructions under each source
line and emits a JSON map plus an optional self-contained HTML report.

Typical use:

  IGC_ShaderDumpEnable=1 IGC_DumpToCustomDir=/abs/dump \
  NEO_CACHE_PERSISTENT=0 NEOReadDebugKeys=1 RebuildPrecompiledKernels=1 \
  ./my_sycl_kernel

  python3 scripts/ikt_source_asm.py --dump-dir /abs/dump \
    --path-map /work=. -o source_asm.json --html source_asm.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections import defaultdict
from pathlib import Path

FILE_RE = re.compile(r"^//\s*File:\s*(.*)$")
LINE_RE = re.compile(r"^//\s*Line\s+(\d+):\s?(.*)$")
KERNEL_RE = re.compile(r"^//\.kernel\s+(.*)$")


def normalize_path(path: str, path_maps: list[tuple[str, str]]) -> str:
    for old, new in path_maps:
        if path == old:
            return new
        if path.startswith(old.rstrip("/") + "/"):
            return new.rstrip("/") + "/" + path[len(old.rstrip("/")) + 1 :]
    return path


def is_instruction(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("//"):
        return False
    if s.endswith(":"):
        return False
    return True


def parse_asm(path: Path, path_maps: list[tuple[str, str]]):
    kernel = path.stem
    current_file = None
    current_line = 0
    current_text = ""
    lines = defaultdict(list)
    texts = {}

    for raw in path.read_text(errors="replace").splitlines():
        s = raw.rstrip()
        m = KERNEL_RE.match(s)
        if m:
            kernel = m.group(1).strip()
            continue
        m = FILE_RE.match(s)
        if m:
            current_file = normalize_path(m.group(1).strip(), path_maps)
            current_line = 0
            current_text = ""
            continue
        m = LINE_RE.match(s)
        if m:
            current_line = int(m.group(1))
            current_text = m.group(2).strip()
            if current_file is not None:
                texts[(current_file, current_line)] = current_text
            continue
        if current_file is None or not is_instruction(s):
            continue
        lines[(current_file, current_line)].append(s)

    files = defaultdict(dict)
    for (file_name, line_no), asm in sorted(lines.items()):
        files[file_name][str(line_no)] = {
            "line": line_no,
            "source": texts.get((file_name, line_no), ""),
            "asm": asm,
            "instruction_count": len(asm),
        }
    return {
        "asm_file": str(path),
        "kernel": kernel,
        "files": files,
    }


def build_report(dump_dir: Path, path_maps, source_root):
    kernels = []
    for path in sorted(dump_dir.glob("*.asm")):
        kernels.append(parse_asm(path, path_maps))

    total_lines = 0
    total_insts = 0
    all_lines = []
    for kernel in kernels:
        kernel_insts = 0
        for file_name, entries in kernel["files"].items():
            for entry in entries.values():
                total_lines += 1
                total_insts += entry["instruction_count"]
                kernel_insts += entry["instruction_count"]
                all_lines.append((entry["instruction_count"], kernel["kernel"], file_name, entry["line"], entry["source"]))
        kernel["instruction_count"] = kernel_insts

    return {
        "tool": "ikt_source_asm",
        "dump_dir": str(dump_dir),
        "source_root": source_root,
        "kernels": kernels,
        "summary": {
            "kernels": len(kernels),
            "source_lines": total_lines,
            "instructions": total_insts,
            "top_lines": [
                {
                    "instructions": n,
                    "kernel": k,
                    "file": f,
                    "line": l,
                    "source": s,
                }
                for n, k, f, l, s in sorted(all_lines, reverse=True)[:20]
            ],
        },
    }


def render_html(report, title: str) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{e(title)}</title>",
        """<style>
body{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px;color:#1f2328;background:#fff}
h1,h2{font-weight:600} h1{font-size:24px} h2{font-size:18px;margin-top:32px}
.meta{color:#57606a;margin-bottom:20px}
table{border-collapse:collapse;width:100%;margin:12px 0 28px}
th,td{border:1px solid #d0d7de;padding:7px 9px;vertical-align:top;text-align:left}
th{background:#f6f8fa;font-weight:600;position:sticky;top:0}
td.line{width:60px;text-align:right;color:#57606a;font-variant-numeric:tabular-nums}
td.src{width:38%;white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
td.asm{width:62%}
pre{margin:0;white-space:pre-wrap;font:12px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace}
tr.ikt td.src{background:#fff8c5}
tr.ikt td.line{background:#fff8c5}
code{font:12px ui-monospace,SFMono-Regular,Consolas,monospace}
.small{font-size:12px;color:#57606a}
</style></head><body>""",
        f"<h1>{e(title)}</h1>",
        f"<div class='meta'>Dump: <code>{e(report['dump_dir'])}</code> · "
        f"kernels: <b>{report['summary']['kernels']}</b> · "
        f"source lines: <b>{report['summary']['source_lines']}</b> · "
        f"ISA instructions: <b>{report['summary']['instructions']}</b></div>",
    ]

    if report["summary"]["top_lines"]:
        parts.append("<h2>Top source lines by ISA instruction count</h2>")
        parts.append("<table><tr><th>Instructions</th><th>Kernel</th><th>File</th><th>Line</th><th>Source</th></tr>")
        for row in report["summary"]["top_lines"]:
            parts.append(
                f"<tr><td class='line'>{row['instructions']}</td><td><code>{e(row['kernel'][:80])}</code></td>"
                f"<td><code>{e(row['file'])}</code></td><td class='line'>{row['line']}</td>"
                f"<td class='src'>{e(row['source'])}</td></tr>"
            )
        parts.append("</table>")

    for kernel in report["kernels"]:
        if not kernel["files"]:
            continue
        parts.append(f"<h2><code>{e(kernel['kernel'])}</code></h2>")
        parts.append(f"<div class='meta'>ISA: <code>{e(kernel['asm_file'])}</code> · "
                     f"instructions: <b>{kernel['instruction_count']}</b></div>")
        for file_name, entries in sorted(kernel["files"].items()):
            parts.append(f"<h2><code>{e(file_name)}</code></h2>")
            parts.append("<table><tr><th>Line</th><th>Source</th><th>ISA</th></tr>")
            for key in sorted(entries, key=lambda x: int(x)):
                entry = entries[key]
                cls = " class='ikt'" if any(x in entry["source"] for x in ("IKT_", "ikt::")) else ""
                asm = "\n".join(entry["asm"])
                parts.append(
                    f"<tr{cls}><td class='line'>{entry['line']}</td>"
                    f"<td class='src'>{e(entry['source'])}</td>"
                    f"<td class='asm'><pre>{e(asm)}</pre></td></tr>"
                )
            parts.append("</table>")
    parts.append("</body></html>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump-dir", type=Path, required=True, help="IGC shader dump directory")
    ap.add_argument("--source-root", default="", help="Source root recorded in the HTML/JSON")
    ap.add_argument("--path-map", action="append", default=[], metavar="FROM=TO",
                    help="Rewrite a recorded source path prefix (repeatable)")
    ap.add_argument("-o", "--output", type=Path, default=None, help="JSON map path")
    ap.add_argument("--html", type=Path, default=None, help="Self-contained HTML report path")
    ap.add_argument("--title", default="IKT source ↔ ISA map")
    args = ap.parse_args()

    if not args.dump_dir.is_dir():
        ap.error(f"not a directory: {args.dump_dir}")

    path_maps = []
    for item in args.path_map:
        if "=" not in item:
            ap.error(f"--path-map must be FROM=TO, got {item!r}")
        old, new = item.split("=", 1)
        path_maps.append((old, new))

    report = build_report(args.dump_dir, path_maps, args.source_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {args.output}")
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(report, args.title))
        print(f"wrote {args.html}")
    if not args.output and not args.html:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

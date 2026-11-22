#!/usr/bin/env python3
"""Build a Compiler Explorer-style source-to-ISA map from IGC ASM dumps.

IGC's final .asm listing is annotated with `// File:` and `// Line N:`
comments. This tool groups the following ISA instructions under each source
line and emits a JSON map plus a self-contained two-pane HTML viewer.

Typical use:

  IGC_ShaderDumpEnable=1 IGC_DumpToCustomDir=/abs/dump \
  NEO_CACHE_PERSISTENT=0 NEOReadDebugKeys=1 RebuildPrecompiledKernels=1 \
  ./my_sycl_kernel

  python3 scripts/ikt_source_asm.py --dump-dir /abs/dump \
    --path-map /work=. --source-root . \
    -o source_asm.json --html source_asm.html
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
    grouped = defaultdict(list)
    texts = {}
    order = 1

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
        key = (current_file, current_line)
        if key not in grouped:
            grouped[key] = {"asm": [], "order": order}
            order += 1
        grouped[key]["asm"].append(s)

    return {
        "asm_file": str(path),
        "kernel": kernel,
        "grouped": grouped,
        "texts": texts,
    }


def source_lines_for(path: str, source_root: Path):
    root = source_root.resolve()
    candidates = []
    p = Path(path)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(source_root / path)
        candidates.append(source_root / path.lstrip("./"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
            if candidate.is_file():
                return candidate.read_text(errors="replace").splitlines()
        except (OSError, ValueError):
            pass
    return None


def build_report(dump_dir: Path, path_maps, source_root: Path):
    kernels = []
    total_lines = 0
    total_insts = 0
    all_top = []

    for asm_path in sorted(dump_dir.glob("*.asm")):
        parsed = parse_asm(asm_path, path_maps)
        files = {}
        kernel_insts = 0

        for file_name in sorted({k[0] for k in parsed["grouped"]}):
            grouped = {k[1]: v for k, v in parsed["grouped"].items() if k[0] == file_name}
            source = source_lines_for(file_name, source_root)
            if source is not None:
                lines = []
                for i, text in enumerate(source, start=1):
                    entry = grouped.get(i)
                    asm = entry["asm"] if entry else []
                    lines.append({
                        "line": i,
                        "source": text,
                        "asm": asm,
                        "instruction_count": len(asm),
                        "order": entry["order"] if entry else 10**9,
                    })
            else:
                lines = []
                for line_no in sorted(grouped):
                    entry = grouped[line_no]
                    lines.append({
                        "line": line_no,
                        "source": parsed["texts"].get((file_name, line_no), ""),
                        "asm": entry["asm"],
                        "instruction_count": len(entry["asm"]),
                        "order": entry["order"],
                    })

            files[file_name] = {
                "path": file_name,
                "source_available": source is not None,
                "lines": lines,
            }
            for entry in lines:
                if entry["instruction_count"]:
                    total_lines += 1
                    total_insts += entry["instruction_count"]
                    kernel_insts += entry["instruction_count"]
                    all_top.append((
                        entry["instruction_count"], parsed["kernel"], file_name,
                        entry["line"], entry["source"],
                    ))

        kernels.append({
            "asm_file": parsed["asm_file"],
            "kernel": parsed["kernel"],
            "instruction_count": kernel_insts,
            "files": files,
        })

    return {
        "tool": "ikt_source_asm",
        "format_version": 2,
        "dump_dir": str(dump_dir),
        "source_root": str(source_root),
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
                for n, k, f, l, s in sorted(all_top, reverse=True)[:20]
            ],
        },
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#ffffff; --fg:#1f2328; --muted:#57606a; --border:#d0d7de; --head:#f6f8fa;
  --accent:#0969da; --accent-bg:#ddf4ff; --ikt:#fff8c5; --asm:#f6f8fa;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
  font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:10;background:var(--head);border-bottom:1px solid var(--border);
  padding:10px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0 12px 0 0;font-weight:600}
header select,header input{font:13px ui-monospace,SFMono-Regular,Consolas,monospace;padding:4px 6px;
  border:1px solid var(--border);border-radius:6px;background:#fff}
header input{min-width:220px}
header label{font-size:12px;color:var(--muted);display:flex;gap:5px;align-items:center}
#status{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap}
.layout{display:grid;grid-template-columns:minmax(360px,45%) 1fr;height:calc(100vh - 57px)}
.pane{overflow:auto;border-right:1px solid var(--border);position:relative}
.pane:last-child{border-right:0}
.pane-head{position:sticky;top:0;z-index:5;background:var(--head);border-bottom:1px solid var(--border);
  padding:6px 10px;font:12px ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--muted)}
.row{display:flex;min-height:20px;border-left:3px solid transparent}
.src-line{cursor:pointer;white-space:pre}
.src-line:hover{background:#f3f4f6}
.src-line.has-asm{background:#fbfcfd}
.src-line.ikt{border-left-color:#bf8700}
.src-line.active{background:var(--accent-bg);border-left-color:var(--accent)}
.src-line.match{outline:2px solid #bf8700;outline-offset:-2px}
.asm-line{cursor:pointer;white-space:pre;background:var(--asm)}
.asm-line:hover{background:#eef1f4}
.asm-line.active{background:var(--accent-bg)}
.asm-line.match{outline:2px solid #bf8700;outline-offset:-2px}
.ln{flex:0 0 52px;text-align:right;padding:0 8px;color:#8c959f;user-select:none;
  font:12px/20px ui-monospace,SFMono-Regular,Consolas,monospace}
.code{flex:1;padding:0 8px;font:12px/20px ui-monospace,SFMono-Regular,Consolas,monospace}
.cnt{flex:0 0 42px;text-align:right;padding:0 8px;color:#8c959f;font:11px/20px ui-monospace,Consolas,monospace}
.idx{flex:0 0 48px;text-align:right;padding:0 6px;color:#8c959f;user-select:none;
  font:11px/20px ui-monospace,SFMono-Regular,Consolas,monospace}
.srcmark{flex:0 0 52px;text-align:right;padding:0 6px;color:var(--accent);user-select:none;
  font:11px/20px ui-monospace,SFMono-Regular,Consolas,monospace}
.text{flex:1;padding:0 8px;font:12px/20px ui-monospace,SFMono-Regular,Consolas,monospace}
.ikt .text{background:var(--ikt)}
.note{padding:8px 12px;color:var(--muted);font-size:12px}
body.ikt-only .src-line:not(.ikt),body.ikt-only .asm-line:not(.ikt){display:none}
@media(max-width:900px){
  .layout{grid-template-columns:1fr;height:auto}
  .pane{height:50vh;border-right:0;border-bottom:1px solid var(--border)}
  header input{min-width:140px}
  #status{margin-left:0;white-space:normal}
}
</style></head>
<body>
<header>
  <h1>__TITLE__</h1>
  <select id="kernel"></select>
  <select id="file"></select>
  <input id="search" type="search" placeholder="Search source or ISA">
  <label><input id="iktOnly" type="checkbox"> only IKT lines</label>
  <span id="status"></span>
</header>
<div class="layout">
  <div class="pane" id="sourcePane"><div class="pane-head" id="sourceHead">Source</div><div id="source"></div></div>
  <div class="pane" id="asmPane"><div class="pane-head" id="asmHead">ISA</div><div id="asm"></div></div>
</div>
<script id="ikt-data" type="application/json">__DATA__</script>
<script>
const report = JSON.parse(document.getElementById('ikt-data').textContent);
const kernelSel = document.getElementById('kernel');
const fileSel = document.getElementById('file');
const sourceEl = document.getElementById('source');
const asmEl = document.getElementById('asm');
const sourceHead = document.getElementById('sourceHead');
const asmHead = document.getElementById('asmHead');
const statusEl = document.getElementById('status');
const searchEl = document.getElementById('search');
const iktOnlyEl = document.getElementById('iktOnly');
let activeLine = null;
let locked = false;

function esc(s){ return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function isIkt(s){ return s.includes('IKT_') || s.includes('ikt::'); }
function fileNames(k){ return Object.keys(k.files).filter(f => k.files[f].lines.some(x => x.asm.length) || k.files[f].source_available); }

function populateKernels(){
  kernelSel.innerHTML = report.kernels.map((k,i) => `<option value="${i}">${esc(k.kernel)}</option>`).join('');
  populateFiles();
}
function populateFiles(){
  const k = report.kernels[Number(kernelSel.value)];
  const files = fileNames(k);
  fileSel.innerHTML = files.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join('');
  render();
}
function activate(line, scroll, lock=false){
  if (locked && !lock) return;
  locked = lock || locked;
  activeLine = line;
  document.querySelectorAll('.active').forEach(e => e.classList.remove('active'));
  document.querySelectorAll(`[data-line="${line}"]`).forEach(e => e.classList.add('active'));
  if (scroll) {
    const s = document.querySelector(`.src-line[data-line="${line}"]`);
    const a = document.querySelector(`.asm-line[data-line="${line}"]`);
    if (s) s.scrollIntoView({block:'nearest'});
    if (a) a.scrollIntoView({block:'nearest'});
  }
  statusEl.textContent = `line ${line}`;
}
function render(){
  activeLine = null;
  locked = false;
  const k = report.kernels[Number(kernelSel.value)];
  const f = k.files[fileSel.value];
  if (!f) { sourceEl.innerHTML=''; asmEl.innerHTML=''; return; }
  sourceHead.textContent = `Source · ${f.path}` + (f.source_available ? '' : ' · full source unavailable');
  const srcRows = f.lines.map(e => {
    const cls = ['src-line', e.instruction_count ? 'has-asm' : '', isIkt(e.source) ? 'ikt' : ''].filter(Boolean).join(' ');
    return `<div class="${cls}" data-line="${e.line}" id="src-${e.line}"><span class="ln">${e.line}</span><span class="code">${esc(e.source)}</span><span class="cnt">${e.instruction_count || ''}</span></div>`;
  }).join('');
  sourceEl.innerHTML = srcRows || '<div class="note">No source lines available.</div>';

  const asmRows = [];
  let idx = 1;
  for (const e of [...f.lines].filter(e => e.asm.length).sort((a,b) => (a.order - b.order) || (a.line - b.line))) {
    for (const text of e.asm) {
      const cls = ['asm-line', isIkt(e.source) ? 'ikt' : ''].filter(Boolean).join(' ');
      asmRows.push(`<div class="${cls}" data-line="${e.line}" id="asm-${idx}"><span class="idx">${idx}</span><span class="srcmark">L${e.line}</span><span class="text">${esc(text)}</span></div>`);
      idx++;
    }
  }
  asmHead.textContent = `ISA · ${k.asm_file} · ${idx-1} instructions`;
  asmEl.innerHTML = asmRows.join('') || '<div class="note">No ISA instructions mapped.</div>';

  sourceEl.querySelectorAll('.src-line').forEach(el => {
    const line = Number(el.dataset.line);
    el.addEventListener('click', () => activate(line, true, true));
    el.addEventListener('mouseenter', () => { if (!locked) activate(line, false); });
    el.addEventListener('mouseleave', () => { if (!locked) { activeLine = null; document.querySelectorAll('.active').forEach(x => x.classList.remove('active')); } });
  });
  asmEl.querySelectorAll('.asm-line').forEach(el => {
    const line = Number(el.dataset.line);
    el.addEventListener('click', () => activate(line, true, true));
    el.addEventListener('mouseenter', () => { if (!locked) activate(line, false); });
    el.addEventListener('mouseleave', () => { if (!locked) { activeLine = null; document.querySelectorAll('.active').forEach(x => x.classList.remove('active')); } });
  });
  applySearch();
  statusEl.textContent = `${f.lines.length} source lines · ${idx-1} ISA instructions` + (f.source_available ? '' : ' · mapped lines only');
}

function applySearch(){
  const q = searchEl.value.trim().toLowerCase();
  document.querySelectorAll('.match').forEach(e => e.classList.remove('match'));
  if (!q) return;
  let first = null;
  for (const el of document.querySelectorAll('.src-line,.asm-line')) {
    if (el.textContent.toLowerCase().includes(q)) {
      el.classList.add('match');
      if (!first) first = el;
    }
  }
  if (first) first.scrollIntoView({block:'center'});
}

kernelSel.addEventListener('change', populateFiles);
fileSel.addEventListener('change', render);
searchEl.addEventListener('input', applySearch);
iktOnlyEl.addEventListener('change', () => document.body.classList.toggle('ikt-only', iktOnlyEl.checked));
populateKernels();
</script>
</body></html>
"""


def render_html(report, title: str) -> str:
    data = json.dumps(report, separators=(",", ":")).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump-dir", type=Path, required=True, help="IGC shader dump directory")
    ap.add_argument("--source-root", type=Path, default=Path("."),
                    help="Root used to resolve recorded source paths")
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

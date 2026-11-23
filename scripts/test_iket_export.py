#!/usr/bin/env python3
"""GPU-free checks for v3 depth decoding and the IKET-shaped export."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("ikt_to_perfetto.py")
EVENT = struct.Struct("<QHHHH")


def write_trace(path: Path, version: int, names: list[str], events: list[tuple[int, int, int]]):
    magic = b"IKT03\0\0\0" if version == 3 else b"IKT02\0\0\0"
    cap = 8
    grid = 1
    wg_base = 0
    wg_count = 1
    sgs_per_wg = 1
    num_regions = len(names)
    event_size = EVENT.size
    reserved = 0
    ns_per_tick = 1.0

    with path.open("wb") as f:
        f.write(magic)
        f.write(
            struct.pack(
                "<9Id",
                version,
                cap,
                grid,
                wg_base,
                wg_count,
                sgs_per_wg,
                num_regions,
                event_size,
                reserved,
                ns_per_tick,
            )
        )
        for name in names:
            raw = name.encode()
            f.write(struct.pack("<I", len(raw)))
            f.write(raw)
        f.write(struct.pack("<I", len(events)))
        for ts, region, type_depth in events:
            f.write(EVENT.pack(ts, region, type_depth, 0, 0))


def run_converter(raw: Path, out_dir: Path):
    perfetto = out_dir / "trace.perfetto.json"
    summary = out_dir / "trace_summary.json"
    iket = out_dir / "trace.iket.json"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(raw),
            "-o",
            str(perfetto),
            "--summary",
            str(summary),
            "--iket-out",
            str(iket),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return (
        json.loads(perfetto.read_text()),
        json.loads(summary.read_text()),
        json.loads(iket.read_text()),
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # v3: tile -> load, exercising a true LIFO stack.
        v3 = tmp_path / "nested.ikttrace"
        write_trace(
            v3,
            3,
            ["tile", "load"],
            [
                (0, 0, 0 | (0 << 8)),  # push tile, depth 0
                (1, 1, 0 | (1 << 8)),  # push load, depth 1
                (2, 1, 1 | (1 << 8)),  # pop load, depth 1
                (3, 0, 1 | (0 << 8)),  # pop tile, depth 0
            ],
        )
        _, summary, iket = run_converter(v3, tmp_path)
        assert summary["format_version"] == 3
        assert summary["spans"] == 2
        assert summary["max_span_depth"] == 1
        assert summary["unmatched_begin"] == 0
        assert summary["unmatched_end"] == 0
        assert iket["stringTable"] == ["tile", "load"]
        assert len(iket["launches"][0]["ranges"]) == 2
        assert len(iket["launches"][0]["warpLifetimes"]) == 1
        assert len(iket["locationTable"]) == 1

        # v2: legacy flat begin/end, no depth byte.
        v2 = tmp_path / "flat.ikttrace"
        write_trace(
            v2,
            2,
            ["load"],
            [
                (0, 0, 0),
                (1, 0, 1),
            ],
        )
        _, summary2, iket2 = run_converter(v2, tmp_path)
        assert summary2["format_version"] == 2
        assert summary2["spans"] == 1
        assert summary2["max_span_depth"] == 0
        assert summary2["unmatched_begin"] == 0
        assert summary2["unmatched_end"] == 0
        assert len(iket2["launches"][0]["ranges"]) == 1

    print("IKT converter checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

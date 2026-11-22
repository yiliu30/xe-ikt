#!/usr/bin/env python3
"""Convert an IKP-XE raw trace to Perfetto and summary JSON."""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path

MAGIC = b"IKPXE02\0"
EVENT = struct.Struct("<QHHHH")  # ts, id, type, wg, sg_info
BEGIN, END, MARK = 0, 1, 2
TYPE_PRIORITY = {BEGIN: 0, MARK: 1, END: 2}

CNAMES = [
    "thread_state_running",
    "good",
    "rail_load",
    "thread_state_runnable",
    "yellow",
    "olive",
    "rail_response",
    "rail_animation",
    "generic_work",
    "thread_state_iowait",
]


def read_u32(f) -> int:
    data = f.read(4)
    if len(data) != 4:
        raise ValueError("truncated u32")
    return struct.unpack("<I", data)[0]


def load_trace(path: Path):
    with path.open("rb") as f:
        magic = f.read(8)
        if magic != MAGIC:
            raise ValueError(f"bad magic {magic!r}; expected {MAGIC!r}")
        version = read_u32(f)
        cap = read_u32(f)
        grid = read_u32(f)
        wg_base = read_u32(f)
        wg_count = read_u32(f)
        sgs_per_wg = read_u32(f)
        num_regions = read_u32(f)
        event_size = read_u32(f)
        _reserved = read_u32(f)
        ns_per_tick = struct.unpack("<d", f.read(8))[0]
        if version != 2:
            raise ValueError(f"unsupported version {version}")
        if event_size != EVENT.size:
            raise ValueError(f"bad event size {event_size}")

        names = []
        for _ in range(num_regions):
            n = read_u32(f)
            names.append(f.read(n).decode("utf-8"))

        num_slots = wg_count * sgs_per_wg
        raw_counts = f.read(4 * num_slots)
        if len(raw_counts) != 4 * num_slots:
            raise ValueError("truncated valid-count table")
        counts = list(struct.unpack(f"<{num_slots}I", raw_counts))

        tracks = defaultdict(list)
        for slot, n in enumerate(counts):
            raw = f.read(n * EVENT.size)
            if len(raw) != n * EVENT.size:
                raise ValueError(f"truncated events for slot {slot}")
            local_wg, sg = divmod(slot, sgs_per_wg)
            wg = wg_base + local_wg
            for ts, region, etype, ev_wg, sg_info in EVENT.iter_unpack(raw):
                tracks[(ev_wg if ev_wg != 0xFFFF else wg, sg)].append(
                    (ts, region, etype, sg_info)
                )

    return {
        "ns_per_tick": ns_per_tick,
        "cap": cap,
        "grid": grid,
        "wg_base": wg_base,
        "wg_count": wg_count,
        "sgs_per_wg": sgs_per_wg,
        "names": names,
        "tracks": tracks,
    }


def placement(sg_info: int):
    return {
        "subgroup": sg_info & 0x1F,
        "slice": (sg_info >> 5) & 0x7,
        "subslice": (sg_info >> 8) & 0x3,
        "eu": (sg_info >> 10) & 0x7,
        "thread": (sg_info >> 13) & 0x7,
    }


def percentile(sorted_values, p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    x = (len(sorted_values) - 1) * p
    lo = int(math.floor(x))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = x - lo
    return float(sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac)


def region_stats(values):
    values = sorted(float(v) for v in values)
    n = len(values)
    if not n:
        return {"count": 0}
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n if n else 0.0
    return {
        "count": n,
        "mean_ns": mean,
        "min_ns": values[0],
        "max_ns": values[-1],
        "std_ns": math.sqrt(var),
        "cv": (math.sqrt(var) / mean) if mean else 0.0,
        "p50_ns": percentile(values, 0.50),
        "p95_ns": percentile(values, 0.95),
        "p99_ns": percentile(values, 0.99),
    }


def convert(input_path: Path, output_path: Path, summary_path: Path | None):
    data = load_trace(input_path)
    ns_per_tick = data["ns_per_tick"]
    names = data["names"]
    tracks = data["tracks"]

    events = []
    per_region = defaultdict(list)
    per_track = {}
    unmatched_begin = 0
    unmatched_end = 0
    total_events = 0

    used_tracks = set()
    for (wg, sg), raw_events in sorted(tracks.items()):
        if not raw_events:
            continue
        raw_events.sort(key=lambda e: (e[0], TYPE_PRIORITY.get(e[2], 9), e[1]))
        origin = min(e[0] for e in raw_events)
        stacks = defaultdict(list)
        track_region = defaultdict(list)
        placement_info = placement(raw_events[0][3])
        used_tracks.add((wg, sg))
        total_events += len(raw_events)

        for ts, region, etype, sg_info in raw_events:
            rel_ns = (ts - origin) * ns_per_tick
            args = {
                "region_id": region,
                "region": names[region] if region < len(names) else f"region_{region}",
                "workgroup": wg,
                "subgroup": sg,
            }
            args.update(placement(sg_info))

            if etype == BEGIN:
                stacks[region].append((rel_ns, sg_info))
            elif etype == END:
                if not stacks[region]:
                    unmatched_end += 1
                    continue
                begin_ns, _begin_info = stacks[region].pop()
                dur_ns = rel_ns - begin_ns
                per_region[region].append(dur_ns)
                track_region[region].append(dur_ns)
                events.append(
                    {
                        "ph": "X",
                        "cat": "IKP-XE",
                        "name": args["region"],
                        "pid": wg,
                        "tid": sg + 2,
                        "ts": begin_ns,
                        "dur": max(dur_ns, 0.0),
                        "cname": CNAMES[region % len(CNAMES)],
                        "args": args,
                    }
                )
            elif etype == MARK:
                events.append(
                    {
                        "ph": "i",
                        "s": "t",
                        "cat": "IKP-XE",
                        "name": args["region"],
                        "pid": wg,
                        "tid": sg + 2,
                        "ts": rel_ns,
                        "args": args,
                    }
                )
            else:
                continue

        unmatched_begin += sum(len(v) for v in stacks.values())
        per_track[f"wg{wg}/sg{sg}"] = {
            "workgroup": wg,
            "subgroup": sg,
            "placement": placement_info,
            "regions": {
                names[r] if r < len(names) else f"region_{r}": region_stats(v)
                for r, v in sorted(track_region.items())
            },
        }

    metadata = []
    for wg, sg in sorted(used_tracks):
        if not any(e.get("pid") == wg and e.get("tid") == 0 for e in metadata):
            metadata.append(
                {
                    "ph": "M",
                    "name": "process_name",
                    "pid": wg,
                    "tid": 0,
                    "args": {"name": f"workgroup {wg}"},
                }
            )
        raw = tracks[(wg, sg)][0]
        p = placement(raw[3])
        metadata.append(
            {
                "ph": "M",
                "name": "thread_name",
                "pid": wg,
                "tid": sg + 2,
                "args": {
                    "name": (
                        f"WG {wg} / SG {sg} | S{p['slice']} SS{p['subslice']} "
                        f"EU{p['eu']} T{p['thread']}"
                    )
                },
            }
        )

    trace = {
        "displayTimeUnit": "ns",
        "otherData": {
            "clock": "per-subgroup relative, not synchronized",
            "nsPerTick": ns_per_tick,
            "cap": data["cap"],
            "grid": data["grid"],
            "workgroupBase": data["wg_base"],
            "workgroupCount": data["wg_count"],
            "subgroupsPerWorkgroup": data["sgs_per_wg"],
        },
        "traceEvents": metadata + events,
    }
    with output_path.open("w") as f:
        json.dump(trace, f, separators=(",", ":"))
        f.write("\n")

    summary = {
        "trace": output_path.name,
        "clock": "per-subgroup relative, not synchronized",
        "ns_per_tick": ns_per_tick,
        "cap": data["cap"],
        "grid": data["grid"],
        "workgroup_base": data["wg_base"],
        "workgroup_count": data["wg_count"],
        "subgroups_per_workgroup": data["sgs_per_wg"],
        "tracks": len(used_tracks),
        "events": total_events,
        "unmatched_begin": unmatched_begin,
        "unmatched_end": unmatched_end,
        "regions": [
            {
                "region": r,
                "name": names[r] if r < len(names) else f"region_{r}",
                **region_stats(per_region[r]),
            }
            for r in sorted(per_region)
        ],
        "by_track": per_track,
    }
    if summary_path is not None:
        with summary_path.open("w") as f:
            json.dump(summary, f, indent=2)
            f.write("\n")

    print(
        f"tracks={len(used_tracks)} events={total_events} "
        f"spans={sum(v['count'] for v in summary['regions'])} "
        f"unmatched_begin={unmatched_begin} unmatched_end={unmatched_end}"
    )
    for r in summary["regions"]:
        print(
            f"  {r['name']:16s} n={r['count']:6d} "
            f"mean={r['mean_ns']:10.2f}ns p50={r['p50_ns']:10.2f}ns "
            f"p95={r['p95_ns']:10.2f}ns"
        )
    print(f"wrote {output_path}")
    if summary_path is not None:
        print(f"wrote {summary_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--summary", type=Path)
    ap.add_argument("--no-summary", action="store_true")
    args = ap.parse_args()

    if args.output is None:
        args.output = args.input.with_suffix(".perfetto.json")
    if args.no_summary:
        summary = None
    elif args.summary is None:
        summary = args.input.with_name(args.input.stem + "_summary.json")
    else:
        summary = args.summary
    convert(args.input, args.output, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Convert an IKT raw trace to Perfetto and summary JSON."""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path

MAGIC_V2 = b"IKT02\0\0\0"
MAGIC_V3 = b"IKT03\0\0\0"
EVENT = struct.Struct("<QHHHH")  # ts, id, type_depth, wg, sg_info
BEGIN, END, MARK = 0, 1, 2
FLAT_DEPTH = 0xFF
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
        if magic == MAGIC_V2:
            version = 2
        elif magic == MAGIC_V3:
            version = 3
        else:
            raise ValueError(f"bad magic {magic!r}; expected IKT02 or IKT03")
        encoded_version = read_u32(f)
        cap = read_u32(f)
        grid = read_u32(f)
        wg_base = read_u32(f)
        wg_count = read_u32(f)
        sgs_per_wg = read_u32(f)
        num_regions = read_u32(f)
        event_size = read_u32(f)
        _reserved = read_u32(f)
        ns_per_tick = struct.unpack("<d", f.read(8))[0]
        if encoded_version != version:
            raise ValueError(
                f"magic/version mismatch: magic={version}, header={encoded_version}"
            )
        if version not in (2, 3):
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
            for ts, region, type_depth, ev_wg, sg_info in EVENT.iter_unpack(raw):
                if version >= 3:
                    etype = type_depth & 0xFF
                    depth = (type_depth >> 8) & 0xFF
                else:
                    etype = type_depth
                    depth = FLAT_DEPTH
                tracks[(ev_wg if ev_wg != 0xFFFF else wg, sg)].append(
                    (ts, region, etype, depth, sg_info)
                )

    return {
        "version": version,
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


def region_name(names, region: int) -> str:
    return names[region] if 0 <= region < len(names) else f"region_{region}"


def build_iket_trace(data, spans, marks, track_order, loc_index, track_last_ns):
    """Build a JSON document shaped like CUTLASS IKET's *.trace.json."""
    names = data["names"]
    max_id = max(
        [len(names) - 1]
        + [span["id"] for span in spans]
        + [mark["id"] for mark in marks]
    )
    string_table = [region_name(names, i) for i in range(max_id + 1)]

    location_table = []
    for wg, sg in track_order:
        raw = data["tracks"][(wg, sg)][0]
        p = placement(raw[4])
        location_table.append(
            {
                "ctaId": [wg, 0, 0],
                "gpcId": p["slice"],
                "smId": p["eu"],
                "tpcId": p["subslice"],
                "warpId": sg,
            }
        )

    ranges = []
    for range_id, span in enumerate(spans, 1):
        loc = loc_index[(span["wg"], span["sg"])]
        ranges.append(
            {
                "startTs": round(span["start_ns"]),
                "endTs": round(span["end_ns"]),
                "rangeNameIdx": span["id"],
                "rangeId": range_id,
                "rangeScope": 0,
                "rangeType": 2,
                "rangeColor": 0xFFFFFFFF,
                "warpLocIdxs": [loc, loc],
                "internalEvents": [],
            }
        )

    warp_lifetimes = []
    for wg, sg in track_order:
        loc = loc_index[(wg, sg)]
        warp_lifetimes.append(
            {
                "startTs": 0,
                "endTs": round(track_last_ns[(wg, sg)]),
                "locIdx": loc,
            }
        )

    markers = []
    for mark in marks:
        markers.append(
            {
                "timestamp": round(mark["ts_ns"]),
                "rangeNameIdx": mark["id"],
                "locIdx": loc_index[(mark["wg"], mark["sg"])],
                "depth": None if mark["depth"] == FLAT_DEPTH else mark["depth"],
            }
        )

    launch = {
        "blockDimX": data["sgs_per_wg"] * 16,
        "blockDimY": 1,
        "blockDimZ": 1,
        "gridDimX": data["grid"],
        "gridDimY": 1,
        "gridDimZ": 1,
        "contextId": 0,
        "enabledCluster": False,
        "gridId": 0,
        "kernelName": "ikt",
        "markers": markers,
        "ranges": ranges,
        "singleClusterActive": False,
        "warpLifetimes": warp_lifetimes,
    }
    return {
        "graphLaunches": [],
        "launches": [launch],
        "locationTable": location_table,
        "meta": {
            "version": 1,
            "clock": "per-subgroup relative, not synchronized",
            "nsPerTick": data["ns_per_tick"],
            "formatVersion": data["version"],
        },
        "stringTable": string_table,
    }


def convert(
    input_path: Path,
    output_path: Path,
    summary_path: Path | None,
    iket_path: Path | None = None,
):
    data = load_trace(input_path)
    ns_per_tick = data["ns_per_tick"]
    names = data["names"]
    tracks = data["tracks"]

    spans = []
    marks = []
    per_region = defaultdict(list)
    per_track = {}
    track_last_ns = {}
    unmatched_begin = 0
    unmatched_end = 0
    total_events = 0

    used_tracks = set()
    for (wg, sg), raw_events in sorted(tracks.items()):
        if not raw_events:
            continue
        raw_events.sort(key=lambda e: (e[0], TYPE_PRIORITY.get(e[2], 9), e[1], e[3]))
        origin = min(e[0] for e in raw_events)
        legacy_stacks = defaultdict(list)
        range_stack = []
        track_region = defaultdict(list)
        placement_info = placement(raw_events[0][4])
        used_tracks.add((wg, sg))
        total_events += len(raw_events)
        last_ns = 0.0

        def add_span(region, begin_ns, end_ns, depth, begin_sg_info):
            dur_ns = max(end_ns - begin_ns, 0.0)
            per_region[region].append(dur_ns)
            track_region[region].append(dur_ns)
            spans.append(
                {
                    "id": region,
                    "name": region_name(names, region),
                    "wg": wg,
                    "sg": sg,
                    "depth": None if depth == FLAT_DEPTH else depth,
                    "start_ns": begin_ns,
                    "end_ns": end_ns,
                    "dur_ns": dur_ns,
                    "sg_info": begin_sg_info,
                }
            )

        for ts, region, etype, depth, sg_info in raw_events:
            rel_ns = (ts - origin) * ns_per_tick
            last_ns = max(last_ns, rel_ns)

            if etype == BEGIN:
                if depth == FLAT_DEPTH:
                    legacy_stacks[region].append((rel_ns, sg_info))
                else:
                    if depth < len(range_stack):
                        unmatched_begin += len(range_stack) - depth
                        range_stack = range_stack[:depth]
                    range_stack.append((region, rel_ns, depth, sg_info))
            elif etype == END:
                if depth == FLAT_DEPTH:
                    if not legacy_stacks[region]:
                        unmatched_end += 1
                        continue
                    begin_ns, begin_sg_info = legacy_stacks[region].pop()
                    add_span(region, begin_ns, rel_ns, FLAT_DEPTH, begin_sg_info)
                else:
                    found = -1
                    if depth < len(range_stack) and range_stack[depth][0] == region:
                        found = depth
                    else:
                        for k in range(len(range_stack) - 1, -1, -1):
                            if range_stack[k][0] == region and range_stack[k][2] == depth:
                                found = k
                                break
                    if found < 0:
                        unmatched_end += 1
                        continue
                    entry = range_stack[found]
                    if len(range_stack) > found + 1:
                        unmatched_begin += len(range_stack) - found - 1
                    range_stack = range_stack[:found]
                    add_span(entry[0], entry[1], rel_ns, entry[2], entry[3])
            elif etype == MARK:
                marks.append(
                    {
                        "id": region,
                        "name": region_name(names, region),
                        "wg": wg,
                        "sg": sg,
                        "depth": None if depth == FLAT_DEPTH else depth,
                        "ts_ns": rel_ns,
                        "sg_info": sg_info,
                    }
                )

        unmatched_begin += sum(len(v) for v in legacy_stacks.values())
        unmatched_begin += len(range_stack)
        track_last_ns[(wg, sg)] = last_ns
        per_track[f"wg{wg}/sg{sg}"] = {
            "workgroup": wg,
            "subgroup": sg,
            "placement": placement_info,
            "regions": {
                region_name(names, r): region_stats(v)
                for r, v in sorted(track_region.items())
            },
        }

    track_order = sorted(used_tracks)
    loc_index = {track: i for i, track in enumerate(track_order)}

    events = []
    for span in spans:
        args = {
            "region_id": span["id"],
            "region": span["name"],
            "workgroup": span["wg"],
            "subgroup": span["sg"],
            "depth": span["depth"],
        }
        args.update(placement(span["sg_info"]))
        events.append(
            {
                "ph": "X",
                "cat": "IKT",
                "name": span["name"],
                "pid": span["wg"],
                "tid": span["sg"] + 2,
                "ts": span["start_ns"],
                "dur": span["dur_ns"],
                "cname": CNAMES[span["id"] % len(CNAMES)],
                "args": args,
            }
        )
    for mark in marks:
        args = {
            "region_id": mark["id"],
            "region": mark["name"],
            "workgroup": mark["wg"],
            "subgroup": mark["sg"],
            "depth": mark["depth"],
        }
        args.update(placement(mark["sg_info"]))
        events.append(
            {
                "ph": "i",
                "s": "t",
                "cat": "IKT",
                "name": mark["name"],
                "pid": mark["wg"],
                "tid": mark["sg"] + 2,
                "ts": mark["ts_ns"],
                "args": args,
            }
        )

    metadata = []
    for wg, sg in track_order:
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
        p = placement(raw[4])
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
            "formatVersion": data["version"],
        },
        "traceEvents": metadata + events,
    }
    with output_path.open("w") as f:
        json.dump(trace, f, separators=(",", ":"))
        f.write("\n")

    max_span_depth = max(
        [0] + [span["depth"] for span in spans if span["depth"] is not None]
    )
    max_mark_depth = max(
        [0] + [mark["depth"] for mark in marks if mark["depth"] is not None]
    )
    max_depth = max(max_span_depth, max_mark_depth)
    summary = {
        "trace": output_path.name,
        "clock": "per-subgroup relative, not synchronized",
        "format_version": data["version"],
        "ns_per_tick": ns_per_tick,
        "cap": data["cap"],
        "grid": data["grid"],
        "workgroup_base": data["wg_base"],
        "workgroup_count": data["wg_count"],
        "subgroups_per_workgroup": data["sgs_per_wg"],
        "tracks": len(used_tracks),
        "events": total_events,
        "spans": len(spans),
        "markers": len(marks),
        "max_depth": max_depth,
        "max_span_depth": max_span_depth,
        "max_mark_depth": max_mark_depth,
        "unmatched_begin": unmatched_begin,
        "unmatched_end": unmatched_end,
        "regions": [
            {
                "region": r,
                "name": region_name(names, r),
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

    if iket_path is not None:
        iket = build_iket_trace(
            data, spans, marks, track_order, loc_index, track_last_ns
        )
        with iket_path.open("w") as f:
            json.dump(iket, f, indent=2)
            f.write("\n")

    print(
        f"tracks={len(used_tracks)} events={total_events} "
        f"spans={len(spans)} markers={len(marks)} "
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
    if iket_path is not None:
        print(f"wrote {iket_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--summary", type=Path)
    ap.add_argument("--no-summary", action="store_true")
    ap.add_argument(
        "--iket-out",
        type=Path,
        help="also write a CUTLASS IKET-shaped trace JSON",
    )
    args = ap.parse_args()

    if args.output is None:
        args.output = args.input.with_suffix(".perfetto.json")
    if args.no_summary:
        summary = None
    elif args.summary is None:
        summary = args.input.with_name(args.input.stem + "_summary.json")
    else:
        summary = args.summary
    convert(args.input, args.output, summary, args.iket_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

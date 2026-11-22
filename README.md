# Intra Kernel Tracer (IKT)

A small, kernel-agnostic intra-kernel tracer for Intel Xe/SYCL kernels, modeled on
[yao-jz/intra-kernel-profiler](https://github.com/yao-jz/intra-kernel-profiler).

**Disclaimer:** This is an independent personal project, not an official Intel project. It is not affiliated with, sponsored by, or endorsed by Intel Corporation.

Instrument a kernel with named regions:

```text
IKT_BEGIN(load) ... IKT_END(load)
IKT_BEGIN(compute) ... IKT_END(compute)
IKT_BEGIN(store) ... IKT_END(store)
```

Each subgroup records timestamped events into its own circular buffer. The host
dumps a compact binary trace, and `scripts/ikt_to_perfetto.py` produces:

- Perfetto / Chrome Trace JSON
- Aggregate summary JSON with per-region and per-track statistics

## CUDA IKP to Xe mapping

| CUDA IKT | Intel Xe / SYCL |
|---|---|
| warp | subgroup |
| `threadIdx.x >> 5` | `sg.get_group_linear_id()` |
| lane 0 | `sg.leader()` |
| `blockIdx.x` | `item.get_group_linear_id()` |
| `%smid` | Xe2 `SR0.0` slice/subslice/EU/thread decode |
| `%globaltimer` | `clock<clock_scope::sub_group>()` |
| `st.global.cs.v4.u32` | 128-bit `sycl::vec<uint32_t,4>` store |
| `cudaMalloc` / `cudaMemcpy` | `sycl::malloc_device` / `queue.memcpy` |
| CUDA host session | `ikt::HostSession` |

## Usage

```cpp
#include "ikt/trace.hpp"

enum Region : uint16_t { kLoad, kCompute, kStore };

ikt::HostSession session(queue);
session.init(/*cap=*/256, /*grid=*/4, /*sgs_per_wg=*/16,
             /*wg_base=*/0, /*wg_count=*/4);
session.set_region_names({"load", "compute", "store"});
session.reset();
session.calibrate();  // warm + calibrate before the measured kernel

const auto gbuf = session.global_buffer();
queue.parallel_for(sycl::nd_range<1>{grid * threads, threads},
    [=](sycl::nd_item<1> item) [[sycl::reqd_sub_group_size(16)]] {
      auto sg = item.get_sub_group();
      IKT_CTX_TYPE(256) ctx;
      IKT_CTX_INIT(ctx, gbuf, item, sg);

      IKT_BEGIN(ctx, gbuf, sg, kLoad);
      // work
      IKT_END(ctx, gbuf, sg, kLoad);

      IKT_FLUSH(ctx, gbuf);
    }).wait_and_throw();

session.write_raw("trace.ikttrace");
```

Convert:

```bash
python3 scripts/ikt_to_perfetto.py trace.ikttrace \
  -o trace.perfetto.json --summary trace_summary.json
```

Open `trace.perfetto.json` in <https://ui.perfetto.dev>.

## Build

```bash
source /opt/intel/oneapi/compiler/2026.1/env/vars.sh
./build.sh
./ikt_regions
python3 scripts/ikt_to_perfetto.py ikt_regions.ikttrace
```

`IKT_DEVICE=bmg-g31` selects the AOT device. CMake is also supported:

```bash
cmake -S . -B build -G Ninja \
  -DCMAKE_CXX_COMPILER=$(command -v icpx) \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

Set `-DIKT_TRACE_ENABLED=0` to compile out the profiling macros.

## API

Device macros:

- `IKT_CTX_TYPE(cap)`
- `IKT_CTX_INIT(ctx, gbuf, item, sg)`
- `IKT_BEGIN/END/MARK(ctx, gbuf, sg, id)`
- `IKT_BEGIN_IF/END_IF(ctx, gbuf, sg, id, condition)`
- `IKT_FLUSH(ctx, gbuf)`

Host:

- `HostSession::init(cap, grid, sgs_per_wg, wg_base, wg_count)`
- `HostSession::set_region_names(...)`
- `HostSession::calibrate()`
- `HostSession::global_buffer()`
- `HostSession::reset()`
- `HostSession::write_raw(path)`

Use `wg_base` / `wg_count` to record a bounded subset of workgroups. Use
`IKT_BEGIN_IF` / `IKT_END_IF` to sample selected loop iterations.

## Trace format

The raw `.ikttrace` file contains:

```text
magic "IKT02\0\0\0", version, cap, grid,
workgroup base/count, subgroups per workgroup,
region names, per-slot valid counts,
16-byte events: {ts, region, type, wg, sg_info}
```

`sg_info` packs subgroup id and Xe2 placement:

```text
[0:4] subgroup, [5:7] slice, [8:9] subslice,
[10:12] EU, [13:15] hardware thread
```

## Limitations

- Xe exposes only subgroup-scope clocks on this stack. Each subgroup track is
  relative to its own first event; do not compare apparent timestamps across
  tracks. Region durations and per-track statistics are valid.
- The calibration is a clock-rate estimate. It is warmed and sampled for
  stability, but absolute nanosecond values are approximate.
- The ring buffer is circular: if a subgroup records more than `cap` events,
  only the last `cap` events are retained.
- The raw binary dump copies `cap * wg_count * sgs_per_wg` events on the host.
  Bound `wg_count` and `cap` for large kernels.
- Xe2 placement decoding uses the BMG SR0.0 layout; other architectures may
  need a different decoder.

## Examples

- `examples/regions.cpp` - standalone multi-region SYCL example
- `examples/fmha/` - validated 75,600-token FMHA capture and Perfetto output
- `integrations/sycl-tla-fmha.patch` - SYCL-TLA FMHA integration patch

## Validation

The included example was run on an Intel Arc Pro B70 (BMG-G31):

- 4 workgroups x 16 subgroups = 64 tracks
- 3 regions: load, compute, store
- 1,536 matched spans, 0 unmatched begin/end events
- Calibration stable at ~0.357 ns/tick across repeated runs

## Next steps

The trace backend is the first layer. The same region IDs can be used for:

- GTPin instruction counts / basic-block attribution
- VTune hardware counters and stall reasons
- Level Zero / system timeline alignment
- An IKT-style HTML explorer

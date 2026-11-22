# FMHA capture

This directory contains a validated 75,600-token BF16 FMHA prefill capture from
an Intel Arc Pro B70:

- 128x64 prefill tile, 16 subgroups
- 32 KV tiles per subgroup
- 16 subgroup tracks
- 16,896 matched spans
- 512 tile spans
- 0 unmatched begin/end events

Files:

- `fmha_bf16_75600_ikp_xe.ikptrace` - raw IKP-XE trace
- `fmha_bf16_75600_ikp_xe.perfetto.json` - Perfetto/Chrome Trace
- `fmha_bf16_75600_ikp_xe_summary.json` - per-region and per-track statistics

Open the Perfetto JSON in <https://ui.perfetto.dev>.

## Selected Results

Mean tile duration: 5.245 us; p95: 5.604 us.

Share of tile wall time:

| Region | Share |
|---|---:|
| k_load | 20.3% |
| out_rescale | 11.4% |
| pv | 9.9% |
| qk | 9.0% |
| v_load | 4.9% |
| softmax | 4.8% |
| k_reorder | 4.5% |
| v_reorder | 2.9% |
| p_pack | 1.8% |
| prefetch | 2.4% |

Instrumented child regions cover about 72% of tile time. The remaining ~28% is
barrier/synchronization and uninstrumented work.

The IKP-XE-enabled build with recording off measured 66.6 TFLOP/s (527 ms) on
the B70. The same 128x64 schedule without instrumentation measures 90.4 TFLOP/s
(388 ms), so use the non-IKP build for performance measurements.

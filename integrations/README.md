# SYCL-TLA FMHA integration

`sycl-tla-fmha.patch` adds IKP-XE region profiling to the SYCL-TLA BF16
HDIM128 FMHA prefill example.

It is based on SYCL-TLA commit `7b56b936b87f799f80dde60c2fd8d11c1128cebb`
and includes:

- The 128x64 prefill tile (`ShapeQK 128x64x32`, `ShapePV 128x32x64`)
- IKP-XE instrumentation in `xe_fmha_fwd_mainloop.hpp`
- `--trace`, `--trace_tiles`, and `--trace_subgroups` plumbing
- CMake option `CUTLASS_ENABLE_BMG_FMHA_IKP_XE`

## Apply

```bash
git worktree add ../sycl-tla-fmha-ikp-xe 7b56b936b87f799f80dde60c2fd8d11c1128cebb
cd ../sycl-tla-fmha-ikp-xe
git apply /path/to/xe-ikt/integrations/sycl-tla-fmha.patch
mkdir -p include/ikp_xe
cp /path/to/xe-ikt/include/ikp_xe/trace.hpp include/ikp_xe/trace.hpp
```

## Build

```bash
source /opt/intel/oneapi/compiler/2026.1/env/vars.sh
cmake -S . -B build-ikp-xe -G Ninja \
  -DCMAKE_CXX_COMPILER=$(command -v icpx) \
  -DCMAKE_BUILD_TYPE=Release \
  -DCUTLASS_ENABLE_SYCL=ON \
  -DCUTLASS_ENABLE_BMG_FMHA_IKP_XE=ON \
  -DCUTLASS_ENABLE_BMG_FMHA_TRACE=OFF \
  -DCUTLASS_ENABLE_EXAMPLES=ON \
  -DCUTLASS_ENABLE_TESTS=OFF \
  -DDPCPP_SYCL_TARGET=intel_gpu_bmg_g31 \
  -DMKL_DIR=/opt/intel/oneapi/mkl/2026.1/lib/cmake/mkl
cmake --build build-ikp-xe \
  --target 06_xe_fmha_fwd_prefill_bfloat16_t_hdim128 -j 8
```

## Capture

```bash
build-ikp-xe/examples/06_bmg_flash_attention/06_xe_fmha_fwd_prefill_bfloat16_t_hdim128 \
  --batch=1 --num_heads_q=12 --num_heads_kv=12 \
  --seq_len_qo=75600 --seq_len_kv=75600 \
  --head_size_qk=128 --head_size_vo=128 \
  --iterations=0 --warmup=0 --verify=0 \
  --trace_subgroups=16 --trace_tiles=32 \
  --trace=fmha_bf16_75600_ikp_xe.ikptrace
```

Convert:

```bash
python3 scripts/ikp_xe_to_perfetto.py fmha_bf16_75600_ikp_xe.ikptrace \
  -o fmha_bf16_75600_ikp_xe.perfetto.json \
  --summary fmha_bf16_75600_ikp_xe_summary.json
```

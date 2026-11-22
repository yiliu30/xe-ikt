# Source-to-ISA mapping example

Generated from the standalone `examples/regions.cpp` kernel on an Intel Arc Pro
B70 with:

```bash
icpx -O2 -g -fsycl -fsycl-targets=spir64_gen \
  -Xsycl-target-backend "-device bmg-g31" \
  -Iinclude examples/regions.cpp -o ikt_regions

IGC_ShaderDumpEnable=1 IGC_DumpToCustomDir=/abs/ikt_dump \
NEO_CACHE_PERSISTENT=0 NEOReadDebugKeys=1 RebuildPrecompiledKernels=1 \
./ikt_regions

python3 scripts/ikt_source_asm.py --dump-dir /abs/ikt_dump \
  --path-map /work=. --source-root . \
  -o ikt_regions_source_asm.json --html ikt_regions_source_asm.html
```

Files:

- `ikt_regions_source_asm.json` - source line / ISA instruction map
- `ikt_regions_source_asm.html` - self-contained report with source and ISA columns

The report contains 4 kernels, 49 source-line groups, and 2,353 ISA
instructions. Lines containing `IKT_` or `ikt::` are highlighted.

Open `ikt_regions_source_asm.html` directly in a browser.

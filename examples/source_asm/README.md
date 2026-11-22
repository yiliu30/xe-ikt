# Source-to-ISA mapping (under development)

This is an experimental, static source-to-ISA mapper for Intel Xe/SYCL kernels.
It parses the `// File:` and `// Line N:` annotations in IGC's final `.asm`
listing and emits:

- A JSON source/ISA map
- A self-contained, Compiler Explorer-style two-pane HTML viewer

It does not yet include dynamic per-line execution counts. Those require joining
GTPin or VTune PC/basic-block data with the DWARF `.debug_line` table.

## Generate

Run from the repository root.

Build with line information:

```bash
icpx -O2 -g -fsycl -fsycl-targets=spir64_gen \
  -Xsycl-target-backend "-device bmg-g31" \
  -Iinclude examples/regions.cpp -o ikt_regions
```

Dump the IGC shader and ISA:

```bash
mkdir -p /abs/ikt_dump
IGC_ShaderDumpEnable=1 IGC_DumpToCustomDir=/abs/ikt_dump \
NEO_CACHE_PERSISTENT=0 NEOReadDebugKeys=1 RebuildPrecompiledKernels=1 \
./ikt_regions
```

Generate the map and viewer:

```bash
python3 scripts/ikt_source_asm.py --dump-dir /abs/ikt_dump \
  --path-map /work=. --source-root . \
  -o examples/source_asm/source_asm.json \
  --html examples/source_asm/source_asm.html
```

Use `--path-map` when the source path recorded in the dump differs from the
checkout path. For example, `/work=.` maps a container path `/work/examples/...`
to `./examples/...`.

## Sample output

- `ikt_regions_source_asm.json` - source line / ISA instruction map
- `ikt_regions_source_asm.html` - self-contained viewer

The checked-in sample was generated from `examples/regions.cpp` on an Intel Arc
Pro B70. It contains 4 kernels, 47 mapped source-line groups, and 2,350 ISA
instructions.

Open `ikt_regions_source_asm.html` directly in a browser. It provides:

- Compiler Explorer-style side-by-side source and ISA panes
- Selectable kernel and source file
- Click-linked source/ISA highlighting
- Search across source and ISA
- An IKT-only filter
- Full source display when `--source-root` can resolve the file

## Notes

- `-g` is required for source-line annotations.
- The tool uses the final ISA listing (`.asm`), not the pre-RA `.isaasm`.
- When `--source-root` cannot resolve a file, the JSON contains only lines that
  IGC annotated with ISA.
- The dumped `.elf` contains DWARF `.debug_line`; a future version can use it to
  attach PC ranges and dynamic execution data.

#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")"
source /opt/intel/oneapi/umf/latest/env/vars.sh
source /opt/intel/oneapi/compiler/2026.1/env/vars.sh
device="${IKP_XE_DEVICE:-bmg-g31}"
icpx -O3 -fsycl -fsycl-targets=spir64_gen \
  -Xsycl-target-backend "-device ${device}" \
  -Iinclude examples/regions.cpp -o ikp_xe_regions

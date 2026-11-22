#include "ikt/trace.hpp"

#include <sycl/sycl.hpp>

#include <cstdio>
#include <vector>

enum Region : uint16_t {
  kLoad = 0,
  kCompute = 1,
  kStore = 2,
};

int main() {
  sycl::queue queue(sycl::gpu_selector_v);
  std::printf("device: %s\n",
              queue.get_device().get_info<sycl::info::device::name>().c_str());

  constexpr uint32_t kGrid = 4;
  constexpr uint32_t kThreads = 256;  // 16 subgroups
  constexpr uint32_t kSgsPerWg = 16;
  constexpr uint32_t kCap = 256;
  constexpr size_t kElements = 1u << 20;

  ikt::HostSession session(queue);
  session.init(kCap, kGrid, kSgsPerWg, 0, kGrid);
  session.set_region_names({"load", "compute", "store"});
  session.reset();
  session.calibrate();
  const auto gbuf = session.global_buffer();

  auto* a = sycl::malloc_device<float>(kElements, queue);
  auto* b = sycl::malloc_device<float>(kElements, queue);
  if (!a || !b) {
    std::fprintf(stderr, "USM allocation failed\n");
    return 1;
  }
  queue.fill(a, 1.0f, kElements).wait_and_throw();
  queue.fill(b, 0.0f, kElements).wait_and_throw();

  queue
      .parallel_for(
          sycl::nd_range<1>{kGrid * kThreads, kThreads},
          [=](sycl::nd_item<1> item) [[sycl::reqd_sub_group_size(16)]] {
            auto sg = item.get_sub_group();
            IKT_CTX_TYPE(kCap) ctx;
            IKT_CTX_INIT(ctx, gbuf, item, sg);

            const size_t stride = kGrid * kThreads;
            const size_t base = item.get_global_linear_id();
            for (int r = 0; r < 8; ++r) {
              const size_t idx = base + size_t(r) * stride;

              IKT_BEGIN(ctx, gbuf, sg, kLoad);
              float v = a[idx];
              IKT_END(ctx, gbuf, sg, kLoad);

              IKT_BEGIN(ctx, gbuf, sg, kCompute);
              for (int i = 0; i < 8; ++i) {
                v = sycl::sin(v) * 1.0001f + 0.5f;
              }
              IKT_END(ctx, gbuf, sg, kCompute);
              IKT_MARK(ctx, gbuf, sg, kCompute);

              IKT_BEGIN(ctx, gbuf, sg, kStore);
              b[idx] = v;
              IKT_END(ctx, gbuf, sg, kStore);
            }

            IKT_FLUSH(ctx, gbuf);
          })
      .wait_and_throw();

  const bool ok = session.write_raw("ikt_regions.ikttrace");
  sycl::free(a, queue);
  sycl::free(b, queue);
  if (!ok) {
    std::fprintf(stderr, "failed to write ikt_regions.ikttrace\n");
    return 1;
  }
  std::printf("wrote ikt_regions.ikttrace\n");
  return 0;
}

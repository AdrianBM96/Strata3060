#include "strata/kernels/cpu/iq_avx2.hpp"
#include "strata/kernels/cpu/expert.hpp"
#define GGML_COMMON_DECL_CPP
#include "ggml-common.h"
#include <chrono>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
using namespace strata::kernels::cpu;
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int main() {
    std::mt19937_64 rng(3);
    const int rows = 2560, n = 640, nb = n / 32; const size_t row_bytes = nb * 18;
    std::vector<uint8_t> w((size_t) rows * row_bytes);
    for (size_t i = 0; i < w.size(); i += 8) { uint64_t r = rng(); memcpy(&w[i], &r, 8); }
    for (size_t i = 0; i + 2 <= w.size(); i += 18) { uint16_t h = 0x2c00; memcpy(&w[i], &h, 2); }
    std::vector<block_q8_0> q(4 * nb);
    for (auto& b : q) { b.d = 0x2c00; for (int k = 0; k < 32; ++k) b.qs[k] = (int8_t) ((int) (rng() % 255) - 127); }
    for (int NT = 1; NT <= 4; ++NT) {
        const void* hq[4]; for (int t = 0; t < NT; ++t) hq[t] = &q[t * nb];
        std::vector<float> o((size_t) NT * rows); float* op[4]; for (int t = 0; t < NT; ++t) op[t] = &o[(size_t) t * rows];
        double best = 1e9;
        for (int rep = 0; rep < 7; ++rep) { double t0 = now(); for (int it = 0; it < 200; ++it) iq4nl256_down_rows(w.data(), row_bytes, n, hq, NT, op, 0, rows); best = std::min(best, now() - t0); }
        printf("IQ4_NL down (18B/32w) NT=%d: %.2f GB/s per core in L2\n", NT, 200.0 * w.size() / best / 1e9);
    }
}

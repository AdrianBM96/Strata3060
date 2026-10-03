// tests/core/iq2s_avx2_test.cpp - the IQ2_S block kernel (row_dot_iq2s) against the generic one (row_dot<22>), bit for
// bit, for every window width the pool uses (1-8 tokens).  CPU only: no GPU, no model, no ggml build - it includes the
// kernel source itself so the two internal kernels can be called side by side.
//
//   g++ -O2 -std=c++20 -mavx2 -mfma -mf16c -Iinclude -Ithird_party/ggml tests/core/iq2s_avx2_test.cpp -o iq2s_avx2_test
//
// Exit 0 = identical; 1 = a mismatch (the first one is printed); 77 = this CPU has no AVX2 (ctest: skipped).
// `iq2s_avx2_test --bench`: the two kernels' speed on this CPU, one core, rows in L2 (an A/B before rebuilding).
#include "../../src/kernels/cpu/iq_avx2.cpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>

namespace strata::kernels::cpu {
namespace {

constexpr int kBlockBytes = 82;
constexpr int kBlocks = 10;   // one gate/up row of the model: n_embd 2560 = 10 x QK_K

void fill_block(uint8_t* b, std::mt19937_64& rng, int pattern) {
    for (int k = 0; k < kBlockBytes; ++k) b[k] = (uint8_t) rng();
    if (pattern == 1) std::memset(b + 2, 0xFF, kBlockBytes - 2);   // every index at its top, every sign set
    if (pattern == 2) std::memset(b + 2, 0x00, kBlockBytes - 2);   // index 0, no signs, the smallest scales
    if (pattern == 3) std::memset(b + 66, 0xAA, 8);                // qh bits 1,3,5,7 only
    // a finite, moderate f16 scale (1/8 .. 2), so a NaN can never make two identical sums compare unequal
    const uint16_t h = (uint16_t) (0x3000 + (rng() % 0x0C00));
    std::memcpy(b, &h, 2);
}

template <int NT>
int check(std::mt19937_64& rng, int rows, int pattern) {
    std::vector<uint8_t> w((size_t) rows * kBlocks * kBlockBytes);
    for (int r = 0; r < rows * kBlocks; ++r) fill_block(&w[(size_t) r * kBlockBytes], rng, pattern);
    std::vector<block_q8_K> q((size_t) NT * kBlocks);
    for (auto& b : q) {
        b.d = 0.001f + (float) (rng() % 1000) * 1e-5f;
        for (int k = 0; k < QK_K; ++k) b.qs[k] = (int8_t) ((int) (rng() % 255) - 127);
    }
    const block_q8_K* y[NT];
    for (int t = 0; t < NT; ++t) y[t] = &q[(size_t) t * kBlocks];
    for (int r = 0; r < rows; ++r) {
        const uint8_t* row = &w[(size_t) r * kBlocks * kBlockBytes];
        float a[NT], b[NT];
        row_dot<22, NT>(row, kBlocks, y, a);
        row_dot_iq2s<NT>(row, kBlocks, y, b);
        for (int t = 0; t < NT; ++t)
            if (std::memcmp(&a[t], &b[t], sizeof(float)) != 0) {
                std::printf("FAIL NT=%d pattern=%d row=%d token=%d: generic %.9g, block %.9g\n", NT, pattern, r, t,
                            (double) a[t], (double) b[t]);
                return 1;
            }
    }
    return 0;
}

template <int NT>
int check_all(std::mt19937_64& rng) {
    int bad = 0;
    for (int pattern = 0; pattern < 4; ++pattern) bad |= check<NT>(rng, pattern == 0 ? 512 : 16, pattern);
    return bad;
}

// The public entry (iq256_rows, type 22) goes through the block kernel unless STRATA_IQ2S_BLOCK=0.
int check_dispatch(std::mt19937_64& rng) {
    constexpr int rows = 8, nt = 3;
    std::vector<uint8_t> w((size_t) rows * kBlocks * kBlockBytes);
    for (int r = 0; r < rows * kBlocks; ++r) fill_block(&w[(size_t) r * kBlockBytes], rng, 0);
    std::vector<block_q8_K> q((size_t) nt * kBlocks);
    for (auto& b : q) {
        b.d = 0.01f;
        for (int k = 0; k < QK_K; ++k) b.qs[k] = (int8_t) ((int) (rng() % 255) - 127);
    }
    const void* act[nt];
    for (int t = 0; t < nt; ++t) act[t] = &q[(size_t) t * kBlocks];
    std::vector<float> out((size_t) nt * rows);
    float* outp[nt];
    for (int t = 0; t < nt; ++t) outp[t] = &out[(size_t) t * rows];
    iq256_rows(22, w.data(), (size_t) kBlocks * kBlockBytes, kBlocks * QK_K, act, nt, outp, 0, rows);
    const block_q8_K* y[nt];
    for (int t = 0; t < nt; ++t) y[t] = &q[(size_t) t * kBlocks];
    for (int r = 0; r < rows; ++r) {
        float b[nt];
        row_dot_iq2s<nt>(&w[(size_t) r * kBlocks * kBlockBytes], kBlocks, y, b);
        for (int t = 0; t < nt; ++t)
            if (std::memcmp(&outp[t][r], &b[t], sizeof(float)) != 0) {
                std::printf("FAIL dispatch row=%d token=%d: %.9g vs %.9g\n", r, t, (double) outp[t][r], (double) b[t]);
                return 1;
            }
    }
    return 0;
}

template <int NT>
void bench() {
    constexpr int rows = 640;   // 640 rows x 820 B = 525 KB: in L2 on most CPUs, so this is the decode cost alone
    std::mt19937_64 rng(5);
    std::vector<uint8_t> w((size_t) rows * kBlocks * kBlockBytes);
    for (int r = 0; r < rows * kBlocks; ++r) fill_block(&w[(size_t) r * kBlockBytes], rng, 0);
    std::vector<block_q8_K> q((size_t) NT * kBlocks);
    for (auto& b : q) {
        b.d = 0.01f;
        for (int k = 0; k < QK_K; ++k) b.qs[k] = (int8_t) ((int) (rng() % 255) - 127);
    }
    const block_q8_K* y[NT];
    for (int t = 0; t < NT; ++t) y[t] = &q[(size_t) t * kBlocks];
    volatile float sink = 0.0f;
    float o[NT];
    auto time = [&](auto&& fn) {
        double best = 1e30;
        for (int rep = 0; rep < 7; ++rep) {
            const auto t0 = std::chrono::steady_clock::now();
            for (int it = 0; it < 200; ++it)
                for (int r = 0; r < rows; ++r) {
                    fn(&w[(size_t) r * kBlocks * kBlockBytes], o);
                    sink = sink + o[0];
                }
            best = std::min(best, std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count());
        }
        return 200.0 * rows * kBlocks * kBlockBytes / best / 1e9;
    };
    const double g = time([&](const uint8_t* row, float* r) { row_dot<22, NT>(row, kBlocks, y, r); });
    const double b = time([&](const uint8_t* row, float* r) { row_dot_iq2s<NT>(row, kBlocks, y, r); });
    std::printf("NT=%d  generic %.2f GB/s  block %.2f GB/s  x%.2f\n", NT, g, b, b / g);
}

}  // namespace
}  // namespace strata::kernels::cpu

int main(int argc, char** argv) {
    using namespace strata::kernels::cpu;
#if defined(__GNUC__) || defined(__clang__)
    if (!__builtin_cpu_supports("avx2") || !__builtin_cpu_supports("fma") || !__builtin_cpu_supports("f16c")) {
        std::printf("SKIP: this CPU has no AVX2/FMA/F16C\n");
        return 77;
    }
#endif
    if (argc > 1 && std::strcmp(argv[1], "--bench") == 0) {
        bench<1>(); bench<2>(); bench<3>(); bench<4>(); bench<6>(); bench<8>();
        return 0;
    }
    int bad = 0;
    for (std::uint64_t seed : {1ull, 7ull, 1234567ull}) {
        std::mt19937_64 rng(seed);
        bad |= check_all<1>(rng) | check_all<2>(rng) | check_all<3>(rng) | check_all<4>(rng) | check_all<5>(rng) |
               check_all<6>(rng) | check_all<7>(rng) | check_all<8>(rng);
    }
    std::mt19937_64 rng(99);
    if (iq2s_block) bad |= check_dispatch(rng);
    if (bad) return 1;
    std::printf("iq2s_avx2_test: block kernel bit-identical to the generic one (NT 1-8, 3 seeds, 4 patterns)%s\n",
                iq2s_block ? "" : " [STRATA_IQ2S_BLOCK=0: dispatch not checked]");
    return 0;
}

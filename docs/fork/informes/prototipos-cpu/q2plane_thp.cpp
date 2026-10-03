// Scratch prototype (NOT in the repo): Q2_0 (GGUF 18 B / 64 w) row dot on AVX2 with "plane" activations.
// Codes 2 bits, byte k holds weights 4k..4k+3 -> 4 AND/shift planes; the activation is stored de-interleaved by stride 4
// once per layer so a plane of codes lines up with a plane of activations (no per-row unpack/unpack-hi/inserts).
#include "strata/kernels/cpu/expert.hpp"
#include <immintrin.h>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
using namespace strata::kernels::cpu;
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

struct PlaneAct {                       // for H = 2560 (20 block pairs) ; 640-wide down activations use 5 pairs
    alignas(32) int8_t pl[20][4][32];   // [pair][plane][A:16 | B:16]
    alignas(32) float asc[20][8];       // [sA0 sA0 sA1 sA1 sB0 sB0 sB1 sB1]
    alignas(32) float hxp[20][8];       // [hxbA 0 0 0 hxbB 0 0 0]
};
static void prep(const ActQ& a, int nblocks, PlaneAct& o) {
    for (int p = 0; p < nblocks / 2; ++p) {
        for (int j = 0; j < 4; ++j)
            for (int k = 0; k < 16; ++k) {
                o.pl[p][j][k] = a.q[(2 * p) * 64 + 4 * k + j];
                o.pl[p][j][16 + k] = a.q[(2 * p + 1) * 64 + 4 * k + j];
            }
        const int bA = 2 * p, bB = 2 * p + 1;
        const float s[8] = {a.scale[2*bA], a.scale[2*bA], a.scale[2*bA+1], a.scale[2*bA+1], a.scale[2*bB], a.scale[2*bB], a.scale[2*bB+1], a.scale[2*bB+1]};
        memcpy(o.asc[p], s, 32);
        const float h[8] = {a.hx[2*bA] + a.hx[2*bA+1], 0, 0, 0, a.hx[2*bB] + a.hx[2*bB+1], 0, 0, 0};
        memcpy(o.hxp[p], h, 32);
    }
}
static inline float hs(__m256 v) {
    __m128 h = _mm_add_ps(_mm256_castps256_ps128(v), _mm256_extractf128_ps(v, 1));
    __m128 s = _mm_add_ps(h, _mm_movehl_ps(h, h));
    return _mm_cvtss_f32(_mm_add_ss(s, _mm_movehdup_ps(s)));
}
template <int NT>
static inline void row_planes(const uint8_t* row, int nblocks, const PlaneAct* const* a, float* res) {
    __m256 acc[NT], corr[NT];
    for (int t = 0; t < NT; ++t) { acc[t] = _mm256_setzero_ps(); corr[t] = _mm256_setzero_ps(); }
    const __m256i m3 = _mm256_set1_epi8(3), ones = _mm256_set1_epi16(1);
    const __m256i dperm = _mm256_setr_epi32(0, 0, 0, 0, 1, 1, 1, 1);
    for (int p = 0; p < nblocks / 2; ++p) {
        const uint8_t* A = row + (size_t) (2 * p) * 18;
        uint16_t hA, hB; memcpy(&hA, A, 2); memcpy(&hB, A + 18, 2);
        const __m128 f = _mm_cvtph_ps(_mm_insert_epi16(_mm_cvtsi32_si128(hA), hB, 1));
        const __m256 wd = _mm256_permutevar8x32_ps(_mm256_castps128_ps256(f), dperm);
        const __m256i pk = _mm256_inserti128_si256(_mm256_castsi128_si256(_mm_loadu_si128((const __m128i*) (A + 2))),
                                                   _mm_loadu_si128((const __m128i*) (A + 18 + 2)), 1);
        const __m256i c0 = _mm256_and_si256(pk, m3);
        const __m256i c1 = _mm256_and_si256(_mm256_srli_epi16(pk, 2), m3);
        const __m256i c2 = _mm256_and_si256(_mm256_srli_epi16(pk, 4), m3);
        const __m256i c3 = _mm256_and_si256(_mm256_srli_epi16(pk, 6), m3);
        for (int t = 0; t < NT; ++t) {
            const int8_t (*pl)[32] = a[t]->pl[p];
            __m256i s = _mm256_maddubs_epi16(c0, _mm256_load_si256((const __m256i*) pl[0]));
            s = _mm256_add_epi16(s, _mm256_maddubs_epi16(c1, _mm256_load_si256((const __m256i*) pl[1])));
            s = _mm256_add_epi16(s, _mm256_maddubs_epi16(c2, _mm256_load_si256((const __m256i*) pl[2])));
            s = _mm256_add_epi16(s, _mm256_maddubs_epi16(c3, _mm256_load_si256((const __m256i*) pl[3])));
            const __m256 si = _mm256_cvtepi32_ps(_mm256_madd_epi16(s, ones));
            acc[t] = _mm256_fmadd_ps(_mm256_mul_ps(wd, _mm256_load_ps(a[t]->asc[p])), si, acc[t]);
            corr[t] = _mm256_fmadd_ps(wd, _mm256_load_ps(a[t]->hxp[p]), corr[t]);
        }
    }
    for (int t = 0; t < NT; ++t) res[t] = hs(acc[t]) - hs(corr[t]);
}

template <int NT>
static void run() {
    std::mt19937_64 rng(11);
    const int nblocks = 40, rows = 640;
    std::vector<uint8_t> w((size_t) rows * nblocks * 18);
    for (size_t i = 0; i < w.size(); i += 8) { uint64_t r = rng(); memcpy(&w[i], &r, 8); }
    for (size_t i = 0; i + 2 <= w.size(); i += 18) { uint16_t h = 0x2c00 + (uint16_t) (rng() % 64); memcpy(&w[i], &h, 2); }
    std::vector<float> xf(2560);
    alignas(64) static ActQ act[8]; alignas(64) static PlaneAct pa[8];
    const ActQ* ap[8]; const PlaneAct* pp[8];
    for (int t = 0; t < NT; ++t) {
        for (auto& v : xf) v = (float) ((int) (rng() % 2001) - 1000) / 500.f;
        act_quant_q8_1_avx2(xf.data(), 2560, act[t]); prep(act[t], nblocks, pa[t]); ap[t] = &act[t]; pp[t] = &pa[t];
    }
    // correctness vs the repo's kernel
    std::vector<float> o_old((size_t) NT * rows), o_new((size_t) NT * rows);
    float* op[NT]; for (int t = 0; t < NT; ++t) op[t] = &o_old[(size_t) t * rows];
    q2_0_gguf_rows_multi_avx2(w.data(), nblocks * 18, nblocks, ap, NT, op, 0, rows);
    double maxrel = 0, num = 0, den = 0;
    for (int r = 0; r < rows; ++r) {
        float res[NT]; row_planes<NT>(&w[(size_t) r * nblocks * 18], nblocks, pp, res);
        for (int t = 0; t < NT; ++t) { const double a = o_old[(size_t) t * rows + r], b = res[t]; num += std::fabs(a - b); den += std::fabs(a); maxrel = std::max(maxrel, std::fabs(a - b) / (std::fabs(a) + 1e-3)); }
    }
    // timing
    float* o2[NT]; for (int t = 0; t < NT; ++t) o2[t] = &o_new[(size_t) t * rows];
    double bo = 1e9, bn = 1e9;
    for (int rep = 0; rep < 7; ++rep) {
        double t0 = now();
        for (int it = 0; it < 300; ++it) q2_0_gguf_rows_multi_avx2(w.data(), nblocks * 18, nblocks, ap, NT, op, 0, rows);
        bo = std::min(bo, now() - t0);
        t0 = now();
        for (int it = 0; it < 300; ++it) for (int r = 0; r < rows; ++r) { float res[NT]; row_planes<NT>(&w[(size_t) r * nblocks * 18], nblocks, pp, res); for (int t = 0; t < NT; ++t) o2[t][r] = res[t]; }
        bn = std::min(bn, now() - t0);
    }
    const double bytes = 300.0 * rows * nblocks * 18;
    printf("Q2_0 AVX2 NT=%d: mean|rel diff| %.2e (max %.2e) | repo %.2f GB/s, planes %.2f GB/s -> x%.2f\n", NT, num / den, maxrel, bytes / bo / 1e9, bytes / bn / 1e9, bo / bn);
}

#include <thread>
#include <atomic>
#include <pthread.h>
#include <sched.h>
#include <sys/mman.h>
template <int NT>
static void stream(int nth, double gb) {
    std::mt19937_64 rng(5);
    const int nblocks = 40, rows = 640;
    const size_t eb = (size_t) rows * nblocks * 18 * 2;           // gate + up of one expert
    const size_t per = std::max<size_t>(1, (size_t) (gb * 1e9 / eb) / nth);
    std::vector<uint8_t*> buf(nth);
    for (int th = 0; th < nth; ++th) {
        void* p = mmap(nullptr, per * eb + (4u<<20), PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
        p = (void*) (((uintptr_t) p + (2u<<20) - 1) & ~(uintptr_t) ((2u<<20) - 1));
        madvise(p, per * eb, getenv("HUGE") ? MADV_HUGEPAGE : MADV_NOHUGEPAGE);
        buf[th] = (uint8_t*) p;
        for (size_t i = 0; i < per * eb; i += 8) { uint64_t r = rng(); memcpy(buf[th] + i, &r, 8); }
        for (size_t i = 0; i + 2 <= per * eb; i += 18) { uint16_t h = 0x2c00; memcpy(buf[th] + i, &h, 2); }
    }
    std::vector<float> xf(2560);
    alignas(64) static ActQ act[8]; alignas(64) static PlaneAct pa[8];
    const ActQ* ap[8]; const PlaneAct* pp[8];
    for (int t = 0; t < NT; ++t) { for (auto& v : xf) v = (float) ((int) (rng() % 2001) - 1000) / 500.f; act_quant_q8_1_avx2(xf.data(), 2560, act[t]); prep(act[t], nblocks, pa[t]); ap[t] = &act[t]; pp[t] = &pa[t]; }
    for (int variant = 0; variant < 2; ++variant) {
        std::vector<double> secs(nth); std::atomic<int> go{0}; std::vector<std::thread> ths;
        for (int th = 0; th < nth; ++th) ths.emplace_back([&, th] {
            cpu_set_t s; CPU_ZERO(&s); CPU_SET(th, &s); pthread_setaffinity_np(pthread_self(), sizeof s, &s);
            std::vector<float> ob((size_t) NT * rows); float* op[NT]; for (int t = 0; t < NT; ++t) op[t] = &ob[(size_t) t * rows];
            while (!go.load()) {}
            double t0 = now();
            for (int rep = 0; rep < 2; ++rep) for (size_t e = 0; e < per; ++e) {
                const uint8_t* b = buf[th] + e * eb;
                for (int half = 0; half < 2; ++half) {
                    const uint8_t* w = b + (size_t) half * rows * nblocks * 18;
                    if (variant == 0) q2_0_gguf_rows_multi_avx2(w, nblocks * 18, nblocks, ap, NT, op, 0, rows);
                    else for (int r = 0; r < rows; ++r) { float res[NT]; row_planes<NT>(w + (size_t) r * nblocks * 18, nblocks, pp, res); for (int t = 0; t < NT; ++t) op[t][r] = res[t]; }
                }
            }
            secs[th] = now() - t0;
        });
        go.store(1); for (auto& t : ths) t.join();
        double mx = 0; for (double s : secs) mx = std::max(mx, s);
        printf("  STREAM %d thr NT=%d %-7s: %.1f GB/s total\n", nth, NT, variant ? "planes" : "repo", 2.0 * per * eb * nth / mx / 1e9);
    }
}
int main() { stream<1>(4, 1.5); }

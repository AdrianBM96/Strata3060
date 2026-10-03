// Scratch prototype (NOT in the repo): IQ2_S AVX2 row dot with block-level vectorized indices / scales / signs.
// Includes the repo's iq_avx2.cpp so the existing generic Fmt32<22> row_dot is the bit-exact reference.
#include "../../../../src/kernels/cpu/iq_avx2.cpp"
#include <chrono>
#include <cstdio>
#include <random>
#include <vector>

namespace strata::kernels::cpu {
namespace {

// IQ2_S block: d(2) | qs[64] (32 grid-low bytes, then 32 sign bytes) | qh[8] | scales[8]  = 82 B
template <int NT, bool GATHER>
inline void row_dot_iq2s_new(const uint8_t* row, int nblocks, const block_q8_K* const* y, float* res) {
    static const char k_sc_shuffle[128] = {
        0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1, 2,2,2,2,2,2,2,2,3,3,3,3,3,3,3,3, 4,4,4,4,4,4,4,4,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,7,7,7,7,7,7,7,7, 8,8,8,8,8,8,8,8,9,9,9,9,9,9,9,9, 10,10,10,10,10,10,10,10,11,11,11,11,11,11,11,11,
        12,12,12,12,12,12,12,12,13,13,13,13,13,13,13,13, 14,14,14,14,14,14,14,14,15,15,15,15,15,15,15,15 };
    const __m128i m4 = _mm_set1_epi8(0xf), m1 = _mm_set1_epi8(1);
    const __m256i bsel = _mm256_setr_epi8(1,2,4,8,16,32,64,(char)0x80, 1,2,4,8,16,32,64,(char)0x80,
                                          1,2,4,8,16,32,64,(char)0x80, 1,2,4,8,16,32,64,(char)0x80);
    const __m256i shuf_sgn = _mm256_setr_epi8(0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1, 2,2,2,2,2,2,2,2,3,3,3,3,3,3,3,3);
    const __m256i one8 = _mm256_set1_epi8(1);
    const __m256i m300 = _mm256_set1_epi32(0x300);
    const __m256i qh_shift = _mm256_setr_epi32(8, 6, 4, 2, 8, 6, 4, 2);
    const __m256i qh_pick0 = _mm256_setr_epi8(0,(char)0x80,(char)0x80,(char)0x80, 0,(char)0x80,(char)0x80,(char)0x80, 0,(char)0x80,(char)0x80,(char)0x80, 0,(char)0x80,(char)0x80,(char)0x80,
                                             1,(char)0x80,(char)0x80,(char)0x80, 1,(char)0x80,(char)0x80,(char)0x80, 1,(char)0x80,(char)0x80,(char)0x80, 1,(char)0x80,(char)0x80,(char)0x80);
    __m256 accf[NT];
    for (int t = 0; t < NT; ++t) accf[t] = _mm256_setzero_ps();
    for (int i = 0; i < nblocks; ++i) {
        const uint8_t* blk = row + (size_t) i * 82;
        rows_ahead(blk + prefetch_ahead);
        // ---- block-level decode: scales for the 8 halves, 32 grid indices, 32 sign bytes
        __m128i st = _mm_set1_epi64x((long long) u64(blk + 74));
        st = _mm_unpacklo_epi8(_mm_and_si128(st, m4), _mm_and_si128(_mm_srli_epi16(st, 4), m4));
        const __m128i scales = _mm_add_epi8(_mm_slli_epi16(st, 1), m1);      // [a0,b0,a1,b1,...]
        // qh[8]: group g (0..31) takes qh[g/4] bits ((g%4)*2, +1) as index bits 8,9.  8 groups per ymm: bytes qh[2m], qh[2m+1]
        alignas(32) uint32_t gi[32];
        const uint64_t qh8 = u64(blk + 66);
        for (int m = 0; m < 4; ++m) {
            const __m128i qh2 = _mm_cvtsi32_si128((int) ((qh8 >> (16 * m)) & 0xffff));       // qh[2m], qh[2m+1]
            const __m256i qhv = _mm256_shuffle_epi8(_mm256_broadcastsi128_si256(qh2), qh_pick0); // lanes 0-3 = qh[2m], 4-7 = qh[2m+1]
            const __m256i hi = _mm256_and_si256(_mm256_sllv_epi32(qhv, qh_shift), m300);
            const __m256i lo = _mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*) (blk + 2 + 8 * m)));
            _mm256_store_si256((__m256i*) (gi + 8 * m), _mm256_or_si256(lo, hi));
        }
        const __m256i sgnall = _mm256_loadu_si256((const __m256i*) (blk + 34));
        __m256i acci[NT];
        for (int t = 0; t < NT; ++t) acci[t] = _mm256_setzero_si256();
        for (int H = 0; H < 8; ++H) {
            const uint32_t* q = gi + 4 * H;
            __m256i g;
            if (GATHER) g = _mm256_i32gather_epi64((const long long*) iq2s_grid, _mm_load_si128((const __m128i*) q), 8);
            else g = _mm256_set_epi64x((long long) iq2s_grid[q[3]], (long long) iq2s_grid[q[2]],
                                               (long long) iq2s_grid[q[1]], (long long) iq2s_grid[q[0]]);
            const __m256i dsel = _mm256_permutevar8x32_epi32(sgnall, _mm256_set1_epi32(H));
            const __m256i sb = _mm256_shuffle_epi8(dsel, shuf_sgn);
            const __m256i sgn = _mm256_or_si256(_mm256_cmpeq_epi8(_mm256_and_si256(sb, bsel), bsel), one8);
            const __m256i sc = _mm256_cvtepi8_epi16(_mm_shuffle_epi8(scales, _mm_loadu_si128((const __m128i*) k_sc_shuffle + H)));
            const int off = 32 * H;
            for (int t = 0; t < NT; ++t) {
                const __m256i yv = _mm256_loadu_si256((const __m256i*) (y[t][i].qs + off));
                acci[t] = _mm256_add_epi32(acci[t], _mm256_madd_epi16(_mm256_maddubs_epi16(g, _mm256_sign_epi8(yv, sgn)), sc));
            }
        }
        const float dx = h2f(u16(blk)) * 0.125f;
        for (int t = 0; t < NT; ++t)
            accf[t] = _mm256_fmadd_ps(_mm256_set1_ps(dx * y[t][i].d), _mm256_cvtepi32_ps(acci[t]), accf[t]);
    }
    for (int t = 0; t < NT; ++t) res[t] = hsum8(accf[t]);
}

}  // namespace
}  // namespace strata::kernels::cpu

using namespace strata::kernels::cpu;
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

template <int NT>
static void run(const char* name) {
    std::mt19937_64 rng(7);
    const int nblocks = 10, rows = 640;
    std::vector<uint8_t> w((size_t) rows * nblocks * 82);
    for (size_t i = 0; i < w.size(); i += 8) { uint64_t r = rng(); memcpy(&w[i], &r, 8); }
    for (size_t i = 0; i + 2 <= w.size(); i += 82) { uint16_t h = 0x2c00; memcpy(&w[i], &h, 2); }
    std::vector<block_q8_K> q(NT * nblocks);
    for (auto& b : q) { b.d = 0.01f; for (int k = 0; k < QK_K; ++k) b.qs[k] = (int8_t) ((int) (rng() % 255) - 127); }
    const block_q8_K* yp[NT];
    for (int t = 0; t < NT; ++t) yp[t] = &q[t * nblocks];
    // exactness
    size_t bad = 0;
    for (int r = 0; r < rows; ++r) {
        float a[NT], b[NT];
        row_dot<22, NT>(&w[(size_t) r * nblocks * 82], nblocks, yp, a);
        row_dot_iq2s_new<NT, true>(&w[(size_t) r * nblocks * 82], nblocks, yp, b);
        for (int t = 0; t < NT; ++t) if (memcmp(&a[t], &b[t], 4) != 0) ++bad;
    }
    // timing (in-L2: 640 rows x 820 B = 525 KB)
    volatile float sink = 0; float o[NT];
    double best_old = 1e9, best_new = 1e9;
    for (int rep = 0; rep < 7; ++rep) {
        double t0 = now();
        for (int it = 0; it < 300; ++it) for (int r = 0; r < rows; ++r) { row_dot<22, NT>(&w[(size_t) r * nblocks * 82], nblocks, yp, o); sink += o[0]; }
        best_old = std::min(best_old, now() - t0);
        t0 = now();
        for (int it = 0; it < 300; ++it) for (int r = 0; r < rows; ++r) { row_dot_iq2s_new<NT, true>(&w[(size_t) r * nblocks * 82], nblocks, yp, o); sink += o[0]; }
        best_new = std::min(best_new, now() - t0);
    }
    const double bytes = 300.0 * rows * nblocks * 82;
    printf("%s NT=%d: mismatching floats %zu/%d | generic %.2f GB/s, new %.2f GB/s -> x%.2f\n", name, NT, bad, rows * NT,
           bytes / best_old / 1e9, bytes / best_new / 1e9, best_old / best_new);
}
int main() {
    run<1>("IQ2_S"); run<2>("IQ2_S"); run<3>("IQ2_S"); run<4>("IQ2_S");
}

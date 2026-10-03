// Scratch micro-benchmark (NOT part of the repo): the repo's AVX2 expert row kernels, fed synthetic bytes.
// Compute-bound mode: one expert's rows (<= ~1.5 MB) repeated, so after the first pass they come from L2/L3.
// Streaming mode: a big arena, each thread reads distinct experts, so it is DRAM-bound.
#include "strata/kernels/cpu/expert.hpp"
#include "strata/kernels/cpu/iq_avx2.hpp"
#define GGML_COMMON_DECL_CPP
#include "ggml-common.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <thread>
#include <vector>
#include <atomic>
#include <pthread.h>
#include <sched.h>
using namespace strata::kernels::cpu;
using Clock = std::chrono::steady_clock;
static double now() { return std::chrono::duration<double>(Clock::now().time_since_epoch()).count(); }

struct Fmt { const char* name; int type; size_t blk_bytes; int blk_vals; };
// gate/up rows: n=2560 values; down rows: n=640 values (Q2_0 down for IQ models; here we bench gate/up rows only for IQ, all three for Q2_0)

static void pin(int cpu) { cpu_set_t s; CPU_ZERO(&s); CPU_SET(cpu, &s); pthread_setaffinity_np(pthread_self(), sizeof s, &s); }

int main(int argc, char** argv) {
    int nthreads = argc > 1 ? atoi(argv[1]) : 1;
    int NT = argc > 2 ? atoi(argv[2]) : 1;           // tokens per group
    double arena_gb = argc > 3 ? atof(argv[3]) : 1.0;
    std::mt19937_64 rng(1);
    // ---- activations: random floats -> ActQ (Q2_0 path) and block_q8_K (IQ path)
    std::vector<float> xf(2560);
    for (auto& v : xf) v = (float) ((int) (rng() % 2001) - 1000) / 500.f;
    alignas(64) static ActQ act[8];
    for (int t = 0; t < 8; ++t) act_quant_q8_1_avx2(xf.data(), 2560, act[t]);
    const ActQ* ap[8]; for (int t = 0; t < 8; ++t) ap[t] = &act[t];
    std::vector<block_q8_K> q8k(10 * 8);   // 8 tokens x 10 blocks
    for (auto& b : q8k) { b.d = 0.01f; for (int i = 0; i < QK_K; ++i) b.qs[i] = (int8_t) ((int) (rng() % 255) - 127); for (int i = 0; i < QK_K/16; ++i) b.bsums[i] = 0; }
    const void* aq[8]; for (int t = 0; t < 8; ++t) aq[t] = &q8k[(size_t) t * 10];

    struct Case { const char* name; int type; size_t row_bytes; };
    Case cases[] = {
        {"Q2_0 (GGUF 18B/64w) gate/up row", 42, 40 * 18},
        {"IQ2_XS  (74B/256w)", 17, 10 * 74},
        {"IQ3_XXS (98B/256w)", 18, 10 * 98},
        {"IQ3_S   (110B/256w)", 21, 10 * 110},
        {"IQ2_S   (82B/256w)", 22, 10 * 82},
        {"IQ4_XS  (136B/256w)", 23, 10 * 136},
    };
    for (auto& c : cases) {
        const size_t rows = 640;                       // gate rows of one expert
        const size_t expert_bytes = rows * c.row_bytes * 2;   // gate + up
        const size_t n_experts = std::max<size_t>(1, (size_t) (arena_gb * 1e9 / (double) expert_bytes) / nthreads);
        const bool stream = arena_gb > 0.1;
        const size_t per_thread = stream ? n_experts : 1;
        std::vector<uint8_t*> bufs(nthreads);
        for (int th = 0; th < nthreads; ++th) {
            bufs[th] = (uint8_t*) aligned_alloc(4096, per_thread * expert_bytes);
            for (size_t i = 0; i < per_thread * expert_bytes; i += 8) { uint64_t r = rng(); memcpy(bufs[th] + i, &r, 8); }
            if (c.type == 42)   // sane fp16 scales (d) at the start of each 18-B block, so no NaN/Inf slows anything
                for (size_t i = 0; i + 18 <= per_thread * expert_bytes; i += 18) { uint16_t h = 0x2c00; memcpy(bufs[th] + i, &h, 2); }
            else   // fp16 d at the start of each 256-value block
                for (size_t i = 0; i + 2 <= per_thread * expert_bytes; i += (c.type==17?74:c.type==18?98:c.type==21?110:c.type==22?82:136)) { uint16_t h = 0x2c00; memcpy(bufs[th] + i, &h, 2); }
        }
        std::atomic<int> go{0};
        std::vector<double> bytes(nthreads, 0), secs(nthreads, 0);
        std::vector<std::thread> ths;
        const int reps = stream ? 2 : 400;
        for (int th = 0; th < nthreads; ++th) ths.emplace_back([&, th] {
            pin(th);
            static thread_local float outbuf[8][640 + 8];
            float* ff[8]; for (int t = 0; t < 8; ++t) ff[t] = outbuf[t];
            while (!go.load()) {}
            double t0 = now(); double total = 0;
            for (int r = 0; r < reps; ++r)
                for (size_t e = 0; e < per_thread; ++e) {
                    const uint8_t* blob = bufs[th] + e * expert_bytes;
                    if (c.type == 42) {
                        q2_0_gguf_rows_multi_avx2(blob, c.row_bytes, 40, ap, NT, ff, 0, 640);
                        q2_0_gguf_rows_multi_avx2(blob + rows * c.row_bytes, c.row_bytes, 40, ap, NT, ff, 0, 640);
                    } else {
                        iq256_gu_rows(c.type, blob, c.row_bytes, rows * c.row_bytes, 2560, aq, NT, ff, 0, 640);
                    }
                    total += (double) expert_bytes;
                }
            secs[th] = now() - t0; bytes[th] = total;
        });
        go.store(1);
        for (auto& t : ths) t.join();
        double tot = 0, mx = 0; for (int th = 0; th < nthreads; ++th) { tot += bytes[th]; mx = std::max(mx, secs[th]); }
        printf("%-34s thr=%d NT=%d %s: %6.2f GB/s total, %5.2f GB/s/thread\n", c.name, nthreads, NT, stream ? "STREAM" : "in-L2 ", tot / mx / 1e9, tot / mx / 1e9 / nthreads);
        for (auto b : bufs) free(b);
    }
}

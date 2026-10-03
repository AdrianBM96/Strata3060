#include "strata/kernels/cpu/expert.hpp"
#include <chrono>
#include <cstdio>
#include <random>
#include <vector>
using namespace strata::kernels::cpu;
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int main() {
    std::mt19937_64 rng(1);
    std::vector<float> x(2560); for (auto& v : x) v = (float) ((int) (rng() % 2001) - 1000) / 500.f;
    alignas(64) static ActQ a;
    for (int n : {2560, 640}) {
        double best = 1e9;
        for (int rep = 0; rep < 9; ++rep) { double t0 = now(); for (int i = 0; i < 20000; ++i) act_quant_q8_1_avx2(x.data(), n, a); best = std::min(best, (now() - t0) / 20000); }
        printf("act_quant_q8_1_avx2 n=%d: %.2f us\n", n, best * 1e6);
    }
}

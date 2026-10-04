// membw.c - la velocidad de LECTURA de la RAM con N hilos, como la leen los expertos en CPU (cada hilo recorre su
// trozo de un buffer mucho mayor que la caché).  Sin dependencias: para comparar antes y después de activar XMP.
//
//     gcc -O2 -mavx2 -pthread membw.c -o membw
//     ./membw            # 1, 2, 4, 6 y 12 hilos sobre 2 GiB
//     ./membw 4096 6     # 4 GiB, solo 6 hilos
#include <immintrin.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct { const uint8_t* p; size_t n; uint64_t sink; } Job;

static void* reader(void* arg) {
    Job* j = (Job*) arg;
    __m256i acc0 = _mm256_setzero_si256(), acc1 = _mm256_setzero_si256();
    for (size_t i = 0; i + 64 <= j->n; i += 64) {
        acc0 = _mm256_xor_si256(acc0, _mm256_load_si256((const __m256i*) (j->p + i)));
        acc1 = _mm256_xor_si256(acc1, _mm256_load_si256((const __m256i*) (j->p + i + 32)));
    }
    uint64_t out[4];
    _mm256_storeu_si256((__m256i*) out, _mm256_xor_si256(acc0, acc1));
    j->sink = out[0] ^ out[1] ^ out[2] ^ out[3];
    return NULL;
}

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double) t.tv_sec + 1e-9 * (double) t.tv_nsec;
}

static double run(const uint8_t* buf, size_t bytes, int nt) {
    pthread_t th[64];
    Job jobs[64];
    const size_t per = (bytes / (size_t) nt) & ~(size_t) 63;
    double best = 0;
    for (int rep = 0; rep < 5; ++rep) {
        const double t0 = now();
        for (int i = 0; i < nt; ++i) {
            jobs[i].p = buf + (size_t) i * per;
            jobs[i].n = per;
            pthread_create(&th[i], NULL, reader, &jobs[i]);
        }
        for (int i = 0; i < nt; ++i) pthread_join(th[i], NULL);
        const double gbs = (double) per * nt / (now() - t0) / 1e9;
        if (gbs > best) best = gbs;
    }
    return best;
}

int main(int argc, char** argv) {
    const size_t mib = argc > 1 ? (size_t) atol(argv[1]) : 2048;
    const int only = argc > 2 ? atoi(argv[2]) : 0;
    const size_t bytes = mib << 20;
    uint8_t* buf = aligned_alloc(64, bytes);
    if (!buf) { fprintf(stderr, "no hay %zu MiB libres\n", mib); return 1; }
    memset(buf, 1, bytes);                                   // las páginas, ya en RAM
    const int counts[] = {1, 2, 4, 6, 12};
    printf("lectura de %zu MiB, la mejor de 5 pasadas\n", mib);
    for (size_t k = 0; k < sizeof counts / sizeof counts[0]; ++k) {
        const int nt = only > 0 ? only : counts[k];
        printf("%3d hilos: %6.1f GB/s\n", nt, run(buf, bytes, nt));
        if (only > 0) break;
    }
    free(buf);
    return 0;
}

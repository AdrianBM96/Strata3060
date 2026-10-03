import numpy as np
from draft_len_sim import simulate
print("How much of the cost-model gain a static --spec-min-p captures (A=30 ms, d=1.2). Columns: best static min_p (of .3 .5 .6 .7 .8) and its gain over .5; then the cost-model rule's gain over .5")
print(" B/A   mu/kappa    best_minp  gain_static  gain_costmodel")
for ba in (0.20, 0.26, 0.35, 0.45):
    A = 30.0; B = A * ba
    for mu, k in ((0.6, .85), (0.7, .85), (0.8, .85), (0.8, .95), (0.9, .95)):
        base = simulate(mu, k, A, B, 1.2, minp=0.5, rounds=120000)
        best = None
        for mp in (0.3, 0.5, 0.6, 0.7, 0.8):
            r = simulate(mu, k, A, B, 1.2, minp=mp, rounds=120000)[0][0]
            if best is None or r > best[1]: best = (mp, r)
        print(" %.2f  %.1f/%.2f     %.1f       %+5.1f%%       %+5.1f%%" % (ba, mu, k, best[0], 100 * (best[1] / base[0][0] - 1), 100 * (base[1][0] / base[0][0] - 1)))

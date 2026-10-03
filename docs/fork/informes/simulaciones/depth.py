import numpy as np
from draft_len_sim import simulate
print("Deeper MTP chains (max drafts 3 vs 5), policy = cost-model rule, per-depth acceptance decay 0.97**j NOT applied (optimistic: same q per depth).")
print("A=30 ms, d=1.2 ms/step")
print(" B/A   mu/kappa   rate(maxd=3)  rate(maxd=5)  gain")
for ba in (0.15, 0.20, 0.26, 0.35):
    A = 30.0; B = A * ba
    for mu, k in ((0.8, .95), (0.9, .95), (0.7, .85)):
        r3 = simulate(mu, k, A, B, 1.2, maxd=3, rounds=120000)[1][0]
        r5 = simulate(mu, k, A, B, 1.2, maxd=5, rounds=120000)[1][0]
        print(" %.2f  %.1f/%.2f    %.4f       %.4f     %+5.1f%%" % (ba, mu, k, r3, r5, 100 * (r5 / r3 - 1)))

import numpy as np
from draft_len_sim import simulate
print("B/A sweep, A=30 ms (3060-like base), d=1.2 ms; gain of the cost-model rule over the per-position dprob>=0.5 rule")
print(" B/A   B     mu=0.6/k=.85  mu=0.7/k=.85  mu=0.8/k=.85  mu=0.8/k=.95  mu=0.9/k=.95   (percent)")
for ba in (0.15, 0.20, 0.26, 0.35, 0.45, 0.55):
    A = 30.0; B = A * ba
    row = []
    for mu, k in ((0.6, .85), (0.7, .85), (0.8, .85), (0.8, .95), (0.9, .95)):
        ra, rb, rf = simulate(mu, k, A, B, 1.2, rounds=120000)
        row.append(100 * (rb[0] / ra[0] - 1))
    print(" %.2f  %4.1f   " % (ba, B) + "   ".join("%+6.1f " % x for x in row))

import numpy as np
rng = np.random.default_rng(7)
def beta_params(mean, conc=3.0): return mean * conc, (1 - mean) * conc
def run(mu, kappa, A, B, d, floor_chain=0.35, minp=0.5, maxd=3, rounds=150000, corr=0.5):
    a, b = beta_params(mu)
    z = rng.beta(a, b, size=rounds); iid = rng.beta(a, b, size=(rounds, maxd))
    dp = corr * z[:, None] + (1 - corr) * iid
    q = np.clip(kappa * dp, 0, 1); ok = rng.random((rounds, maxd)) < q
    cost = lambda T: A + B * (T - 1)
    def stats(T, steps):
        acc = np.zeros(rounds, dtype=int); alive = np.ones(rounds, dtype=bool)
        for j in range(maxd):
            alive = alive & (j < T - 1) & ok[:, j]; acc += alive
        ms = cost(T) + d * steps
        return (1 + acc).sum() / ms.sum()
    # A: current
    Ta = np.ones(rounds, dtype=int); sa = np.ones(rounds, dtype=int); alive = np.ones(rounds, dtype=bool)
    for j in range(maxd):
        inc = alive & (dp[:, j] >= minp); Ta += inc; alive = inc
        if j + 1 < maxd: sa += alive
    # B2: chain floor, then argmax over T with kappa-calibrated q
    Tb = np.ones(rounds, dtype=int); sb = np.ones(rounds, dtype=int); run_ = np.ones(rounds, dtype=bool)
    cum = np.ones(rounds); E = np.ones(rounds); best = 1 / cost(1) * np.ones(rounds)
    for j in range(maxd):
        cum_j = cum * q[:, j]; En = E + cum_j; r = En / cost(j + 2)
        take = run_ & (r > best); Tb = np.where(take, j + 2, Tb); best = np.where(take, r, best)
        cum = np.where(run_, cum_j, cum); E = np.where(run_, En, E)
        run_ = run_ & (dp[:, j] >= floor_chain)
        if j + 1 < maxd: sb += run_
    return stats(Ta, sa), stats(Tb, sb)
print("simple version (chain floor 0.35 + post-hoc argmax over T), gain over the current rule; A=30, d=1.2")
print(" B/A   mu/kappa:  0.6/.85  0.7/.85  0.8/.85  0.8/.95  0.9/.95")
for ba in (0.20, 0.26, 0.35, 0.45):
    A = 30.0; B = A * ba
    row = []
    for mu, k in ((0.6, .85), (0.7, .85), (0.8, .85), (0.8, .95), (0.9, .95)):
        ra, rb = run(mu, k, A, B, 1.2)
        row.append(100 * (rb / ra - 1))
    print(" %.2f             " % ba + "  ".join("%+5.1f" % x for x in row))

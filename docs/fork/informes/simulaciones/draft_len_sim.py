"""Monte Carlo of the window-length policy: current rule (per-position dprob >= min_p, max 3 drafts) versus a
cost-model rule (maximise E[tokens]/cost over the window sizes the chain offers, using calibrated q = kappa*dprob).
Model-based estimate only: acceptance model and costs are ASSUMED parameters (see the report)."""
import numpy as np, sys
rng = np.random.default_rng(1)

def beta_params(mean, conc=3.0):
    return mean * conc, (1 - mean) * conc

def simulate(mu, kappa, A, B, d, minp=0.5, maxd=3, rounds=200000, corr=0.5, cost_shape=None):
    """cost of a round with window T = A + B*(T-1) (verify+commit) + d * (draft steps run for the NEXT round)."""
    a, b = beta_params(mu)
    # shared round difficulty: mix an iid draw with a round-level draw
    z = rng.beta(a, b, size=rounds)
    iid = rng.beta(a, b, size=(rounds, maxd))
    dp = corr * z[:, None] + (1 - corr) * iid          # dprob of draft j (conditional top-1 prob)
    q = np.clip(kappa * dp, 0, 1)                      # true acceptance given prefix accepted
    u = rng.random((rounds, maxd))
    ok = u < q                                          # would draft j be accepted if tested
    def cost(T): return A + B * (T - 1)
    res = {}
    # policy A: current rule.  chain computes draft j+1 only while dprob_j >= minp (draft steps = 1 + that)
    T_a = np.ones(rounds, dtype=int); steps_a = np.ones(rounds, dtype=int)
    alive = np.ones(rounds, dtype=bool)
    for j in range(maxd):
        inc = alive & (dp[:, j] >= minp)
        T_a += inc
        alive = inc
        if j + 1 < maxd: steps_a += alive            # next chain step runs only if this draft passed the floor
    # policy B: choose T in 1..(drafts available) maximising E/cost with q-hat = q (calibrated); chain stops when
    # the cumulative probability (times a typical next q of 0.8) cannot pay: cum*0.8 < E*c
    T_b = np.ones(rounds, dtype=int); steps_b = np.ones(rounds, dtype=int)
    cum = np.ones(rounds); E = np.ones(rounds)
    bestrate = 1.0 / cost(1) * np.ones(rounds)
    avail = np.ones(rounds, dtype=int)
    # first the chain length: draft 0 is always computed (the round graph); draft j+1 only if worthwhile
    run = np.ones(rounds, dtype=bool)
    for j in range(maxd):
        # drafts 0..j computed (run mask); tentative window sizes up to j+2
        cum_j = cum * q[:, j]
        Enew = E + cum_j
        rate = Enew / cost(j + 2)
        take = run & (rate > bestrate)
        T_b = np.where(take, j + 2, T_b)
        bestrate = np.where(take, rate, bestrate)
        cum = np.where(run, cum_j, cum); E = np.where(run, Enew, E)
        # compute the next chain step only if a typical next draft could still pay
        c_next = B / cost(j + 2)
        run = run & (cum * 0.8 >= E * c_next * 0.6)
        if j + 1 < maxd: steps_b += run
    def stats(T, steps):
        # accepted drafts among first T-1: prefix of ok
        acc = np.zeros(rounds, dtype=int); alive2 = np.ones(rounds, dtype=bool)
        for j in range(maxd):
            tested = alive2 & (j < T - 1)
            alive2 = tested & ok[:, j]
            acc += alive2
        tokens = 1 + acc
        ms = A + B * (T - 1) + d * steps
        return tokens.sum() / ms.sum(), tokens.mean(), T.mean()
    ra = stats(T_a, steps_a); rb = stats(T_b, steps_b)
    # reference: plain decode (T=1, no drafts), and fixed T=4 / T=3 / T=2
    rates = {}
    for Tf in (1, 2, 3, 4):
        Tt = np.full(rounds, Tf); st = np.full(rounds, max(Tf - 1, 0))
        rates[Tf] = stats(Tt, st)[0]
    return ra, rb, rates

if __name__ == "__main__":
    scen = [
        ("5070 default split  A=19 B=5.0 d=0.7", 19.0, 5.0, 0.7),
        ("5070 all-CPU        A=19 B=10.5 d=0.7", 19.0, 10.5, 0.7),
        ("3060+AVX2 (low)     A=32 B=7.8 d=1.2", 32.0, 7.8, 1.2),
        ("3060+AVX2 (high)    A=36 B=9.4 d=1.2", 36.0, 9.4, 1.2),
    ]
    for name, A, B, d in scen:
        print("\n==", name)
        print("  mu   kappa | tok/round(A) tok/round(B) | rate A   rate B   gain B/A | best fixed T | plain")
        for mu in (0.6, 0.7, 0.8, 0.9):
            for kappa in (0.95, 0.85):
                ra, rb, rf = simulate(mu, kappa, A, B, d)
                bestT = max(rf, key=rf.get)
                print("  %.2f  %.2f  |  %.2f / T%.2f   %.2f / T%.2f | %.4f  %.4f  %+5.1f%% | T=%d (%.4f, vs A %+5.1f%%) | %.4f  spec-speedup A=%.2fx" % (
                    mu, kappa, ra[1], ra[2], rb[1], rb[2], ra[0], rb[0], 100 * (rb[0] / ra[0] - 1), bestT, rf[bestT],
                    100 * (rf[bestT] / ra[0] - 1), rf[1], ra[0] / rf[1]))

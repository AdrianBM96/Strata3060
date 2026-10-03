# KV / context VRAM model (bytes per context token), 12 GB card, Q2_0. All constants from the source (see report).
HEAD_DIM, NKV, IDX_DIM, IDX_BLOCK, PAGE = 256, 2, 128, 4, 4
def cell_bytes(fmt):
    if fmt == "fp16": return NKV * HEAD_DIM * 2 * 2           # 2048
    if fmt == "int8": return NKV * HEAD_DIM * 2 + NKV * (HEAD_DIM // 64) * 2 * 2   # 1056
    if fmt == "q4_0": return NKV * (HEAD_DIM // 32) * 18 * 2   # 576
    if fmt == "k8v4": return NKV * HEAD_DIM + NKV * (HEAD_DIM // 64) * 2 + NKV * (HEAD_DIM // 32) * 18  # 816
IDX = IDX_DIM * 4 // IDX_BLOCK     # 128 B per cell per layer (fp32 pooled key per 4 cells)
PT = 4 // PAGE                      # page table 4 B per page of 4 cells = 1 B per cell
ROPE = 32 * 4 * 2                   # 256 B per cell, once
def per_token(fmt, layers=12, drafter_full=True):
    b = layers * (cell_bytes(fmt) + IDX + PT) + ROPE
    if drafter_full: b += cell_bytes("int8" if fmt == "k8v4" else fmt) + IDX + PT
    return b
for f in ("fp16", "int8", "q4_0", "k8v4"):
    print("%-5s cell %5d B/layer;  per context token (12 layers + drafter, no streaming): %6d B = %.2f MB per 1K tokens" % (f, cell_bytes(f), per_token(f), per_token(f) * 1024 / 1e6))
# streaming (int8, --kv-resident 32768): fixed + per token
RES = 32768
fixed = 12 * RES * cell_bytes("int8") + (RES + 4 * 6 + 64) * (cell_bytes("int8"))   # 12 layers' resident pools + drafter ring (1 layer)
per_tok_stream = 12 * IDX + 12 * PT + ROPE + cell_bytes("int8") + 6          # indexer (full) + page tables + rope + one-layer staging pool + verify scores (T=6)
print("\nstreaming int8: fixed %.0f MB + %d B/token" % (fixed / 1e6, per_tok_stream))
GDN = 36 * (128 * 48 * 128 + 10240 * 3) * 4
print("GDN state: %d B = %.1f MiB (fixed, 36 layers)" % (GDN, GDN / 2**20))
EXP = 1.416e6; E1K = 4517   # bytes per Q2_0 expert (calibrated on the 32K point), experts at 1K ctx (paper Table 4)
def experts(v_bytes, base=per_token("int8") * 1024): return E1K - (v_bytes - base) / EXP
print("\n ctx   | KV VRAM no-stream | experts(model) | measured (paper T4, 0.1.14) | KV VRAM streamed | experts streamed(model) | host RAM for KV copy")
meas = {4096: 4464, 32768: 4174, 65536: 3809, 131072: 3079, 262144: 1588}
for ctx in (4096, 8192, 16384, 32768, 65536, 98304, 131072, 196608, 262144, 393216, 524288):
    v = per_token("int8") * ctx
    vs = fixed + per_tok_stream * ctx
    e_ns = experts(v); e_s = experts(vs)
    host = ctx * 13 * cell_bytes("int8")
    print(" %6dK | %7.0f MB | %6.0f | %s | %7.0f MB | %6.0f | %.2f GB" % (ctx // 1024, v / 1e6, e_ns, str(meas.get(ctx, "-")).rjust(5), vs / 1e6, e_s, host / 1e9))
print("\nexperts per 1K tokens of context (no streaming): %.1f ; with streaming (marginal): %.2f" % (per_token("int8") * 1024 / EXP, per_tok_stream * 1024 / EXP))

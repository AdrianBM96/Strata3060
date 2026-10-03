import json
H=4096; E=288; K=8; MI=2048; NL=45; NMOE=42; VOC=154880
exp = 3*H*MI
print("params per expert", exp, f"{exp/1e6:.2f}M")
routed_main = exp*E*NMOE
routed_mtp = exp*E
print("routed experts main", routed_main/1e9, "B ; mtp", routed_mtp/1e9)
# KDA layer
kda = 4*(8192*4096) + (128*4096+8192*128)*2 + 64*4096 + 3*8192*4 + 64 + 8192 + 128
dsa_attn = 1536*4096 + 16384*1536 + 512*4096 + 32768*512 + 4096*16384 + 1536+512
idx = 4096*1536 + 128*4096 + 32*4096 + 128*4096 + 4*128 + 256
dsa = dsa_attn+idx
dense = 3*12288*4096
shared = exp
router = E*4096 + E
mhc = 2*(24*16384+24+3)
norms = 2*4096
emb = VOC*H; head = VOC*H
mtp_extra = 4096*8192 + 3*4096
print("KDA layer params", kda/1e6, "DSA layer params (attn+idx)", dsa/1e6, "(idx only",idx/1e6,")")
# layers 0..44 : DSA at 3,7,...,43 (11) ; KDA others (34)
n_dsa=11; n_kda=34
tot_attn = n_kda*kda + n_dsa*dsa
tot_dense = 3*dense
tot_shared = 42*shared
tot_router = 42*router
tot_mhc = 45*mhc
mtp_layer_nonexp = dsa + shared + router + mhc*0 + mtp_extra + norms
nonexp = tot_attn + tot_dense + tot_shared + tot_router + tot_mhc + emb + head + mtp_layer_nonexp
# vision approx
vis = 24*(4*1024*1024 + 3*1024*4096) + 0.57e9*0  # blocks only
print("attn total", tot_attn/1e9, "dense", tot_dense/1e9, "shared", tot_shared/1e9, "router", tot_router/1e6, "M mhc", tot_mhc/1e6, "M emb", emb/1e9, "head", head/1e9, "mtp nonexp", mtp_layer_nonexp/1e9)
print("non-expert text total (incl emb/head/mtp-nonexp):", nonexp/1e9)
total_text = nonexp + routed_main + routed_mtp
print("TEXT total (B):", total_text/1e9, " vision blocks ~", vis/1e9, " -> sum", (total_text+vis)/1e9, " vs safetensors total 321.32B")
# active per token
act_routed = 42*K*exp
act = act_routed + tot_attn + tot_dense + tot_shared + tot_router + head + tot_mhc  # emb is lookup
print("active/token: routed", act_routed/1e9, " total ~", act/1e9, "B (official 18B)")
# GPU-resident non-expert (excluding embedding table, vision, MTP layer)
res = tot_attn + tot_dense + tot_shared + tot_router + head + tot_mhc
print("GPU resident non-expert params (no emb, no MTP, no vision):", res/1e9, "B")
for name,bpw in [("BF16",16),("Q8_0",8.5),("Q6_K",6.56),("Q4_K",4.5),("mixed Q8 attn + keep ~1GB BF16 sens.",None)]:
    if bpw: print(f"  resident @ {name}: {res*bpw/8/1e9:.2f} GB")
# KDA parts that are 'precision sensitive'
# expert bytes
print()
print("bpw    bytes/expert(MB)  per-layer(GB)  experts main(GB)  +MTP layer(GB)  total experts(GB) | per-token read all-active(GB, 336 acts)")
for name,bpw in [("IQ1_S",1.56),("Q2_0 (Strata paper 2.25)",2.25),("IQ2_XS",2.31),("IQ2_XXS",2.06),("IQ3_XXS",3.06),("Q3_K",3.44),("IQ4_XS",4.25),("Q4_K_M",4.8),("FP8",8.0)]:
    be = exp*bpw/8
    layer = be*E
    main = layer*42
    mtp = layer
    print(f"{name:26s} {bpw:5.2f} {be/1e6:8.2f} {layer/1e9:8.2f} {main/1e9:9.1f} {mtp/1e9:8.2f} {(main+mtp)/1e9:9.1f} | {42*K*be/1e9:6.2f}")

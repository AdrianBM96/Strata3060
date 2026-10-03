D=10240; LR=320; N=2560
gr_read = (LR*D*2 + LR*D*2 + 4*D*2)   # down + up + inject bf16
print("GR read bytes", gr_read/1e6, "MB; per layer x2:", 2*gr_read/1e6, "MB; x48:", 48*2*gr_read/1e9, "GB")
# GDN weights (weighted avg bpw from layer.cpp comments)
qkv_bpw=(13*4.25+18*3.4375+4*4.5+1*2.25)/36
gate_bpw=(25*3.4375+4*4.25+3*4.5+4*2.25)/36
print("bpw qkv",qkv_bpw,"gate",gate_bpw)
qkv=10240*N*qkv_bpw/8; z=6144*N*gate_bpw/8; out=N*6144*3.7/8
print("GDN mmvq MB: qkv %.1f z %.1f out %.1f sum %.1f"%(qkv/1e6,z/1e6,out/1e6,(qkv+z+out)/1e6))
state=128*48*128*4
print("GDN state MB", state/1e6, "x36 =", 36*state/1e6, "MB")
alpha_beta=2*48*N*2
router=512*N*2
print("alpha+beta MB",alpha_beta/1e6,"router MB",router/1e6)
P_gdn = 2*gr_read + qkv+z+out + state + alpha_beta + router
print("P bytes GDN layer MB", P_gdn/1e6)
# QSA layer
q=12288*N*3.6/8; kv=2*512*N*3.6/8; o=N*6144*3.6/8; idxq=512*N*2; idxk=128*N*2
cells=2051; kvb=1056
kvread=3.2*cells*kvb
P_qsa = 2*gr_read + q+kv+o+idxq+idxk+router+kvread
print("QSA MB: q %.1f kv %.1f o %.1f idxq %.1f idxk %.2f kvread %.1f  P_qsa %.1f"%(q/1e6,kv/1e6,o/1e6,idxq/1e6,idxk/1e6,kvread/1e6,P_qsa/1e6))
Pw = 36*P_gdn+12*P_qsa
print("P total GB/window", Pw/1e9, " + head 0.44 =", (Pw+0.44e9)/1e9)
for bw,name in ((360e9,"3060"),(672e9,"5070")):
    for eff in (1.0,0.85,0.7):
        print(name, "eff",eff, "P ideal ms %.2f"%((Pw+0.44e9)/(bw*eff)*1e3))
# experts hit
expert_mb = 34.0e9/24576
print("expert MB",expert_mb/1e6)
for T,distinct in ((1,7.2),(3.2,16),(4,19)):
    print("T",T,"cached experts GB/window", distinct*expert_mb*48/1e9)
# per-round bytes
dense=3.54e9; hit=1.06e9; gdn=36*state; kvr=12*kvread; drafts=0.84e9
tot=dense+hit+gdn+kvr+drafts
print("round bytes GB %.2f ; per token (3.23) %.2f"%(tot/1e9, tot/3.23/1e9))
for bw in (360e9,672e9):
    print("round roofline ms", tot/bw*1e3)
# scaling ranges for round time (5070 baseline Table 5: GPU 14.6 CPU 13.4 draft 2.0 other 4.3)
for s in (1.41,1.87,2.42):
    P=14.6*s; draft=2.0*s; other=4.3
    # CPU wait: C - Q hidden; assume wait shrinks by Q growth: Q5070 ~ 57us/layer*48=2.7ms
    Q=2.7*s
    cpu_wait = max(0, 13.4+2.7 - Q)
    rnd=P+cpu_wait+draft+other
    print("scale %.2f: round %.1f ms -> %.1f tok/s (3.23 tok/round)"%(s,rnd,3.23/rnd*1e3))

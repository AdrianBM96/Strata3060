exp_params=25_165_824
ACT=42*8            # expert activations per token (main layers)
BW_GPU=360.0*0.8    # RTX 3060 12GB: 360 GB/s peak, ~80% achievable
def be(bpw): return exp_params*bpw/8/1e6   # MB
def run(name,bpw,n_experts_total,ram_gb,vram_cache_gb,nonexp_gb,hit,bw_cpu,bw_ssd=None,mtp=1.2,ovh_ms=6):
    e_mb=be(bpw); tot_gb=n_experts_total*e_mb/1e3
    cached=int(vram_cache_gb*1e3/e_mb)
    per_tok_gb=ACT*e_mb/1e3
    miss_gb=(1-hit)*per_tok_gb
    ram_budget=max(ram_gb-10,0)    # 10 GB for OS + apps + CPU-side buffers + KV in RAM
    in_ram=min(ram_budget,tot_gb-vram_cache_gb)
    ssd_frac=max(0.0,1-(in_ram+vram_cache_gb)/tot_gb)
    t_cpu=miss_gb*(1-ssd_frac)/bw_cpu*1e3
    t_ssd=(miss_gb*ssd_frac/bw_ssd*1e3) if (bw_ssd and ssd_frac>0) else 0
    t_gpu=(nonexp_gb+hit*per_tok_gb)/BW_GPU*1e3
    t=max(t_cpu,t_gpu)+t_ssd+ovh_ms
    return dict(name=name,e_mb=e_mb,tot_gb=tot_gb,cached=cached,cached_pct=cached/n_experts_total*100,per_tok=per_tok_gb,miss=miss_gb,ssd_frac=ssd_frac,t_cpu=t_cpu,t_gpu=t_gpu,t_ssd=t_ssd,tps=1000/t,tps_mtp=1000/t*mtp)
def show(r):
    print(f"{r['name']:48s} exp={r['e_mb']:5.2f}MB tot={r['tot_gb']:6.1f}GB cache={r['cached']:5d}({r['cached_pct']:.1f}%) tok={r['per_tok']:.2f}GB miss={r['miss']:.2f}GB ssd={r['ssd_frac']*100:4.0f}% | cpu={r['t_cpu']:5.0f} gpu={r['t_gpu']:4.0f} ssd={r['t_ssd']:5.0f} ms -> {r['tps']:5.1f} t/s (MTP x1.2: {r['tps_mtp']:5.1f})")
# VRAM budget: usable 11.0 GB; non-expert resident Q4_K 5.3 / Q6_K 7.4 / Q8_0 9.5 ; KV+state 0.4 ; buffers 0.5
usable=11.0
for lab,ne in (("Q4_K",5.3),("Q6_K",7.4),("Q8_0",9.5)):
    print(lab,"non-expert -> free for experts:", round(usable-ne-0.4-0.5,1),"GB")
print()
free_q4=usable-5.3-0.4-0.5
print("=== FULL model (12,096 experts), IQ2_XS 2.31 bpw, non-expert Q4_K, cache %.1f GB ==="%free_q4)
for ram,ssd_lab,ssd in ((128,'-',None),(64,'NVMe PCIe3 3.0GB/s',3.0),(64,'NVMe PCIe4 5.5GB/s',5.5),(32,'NVMe PCIe3 3.0GB/s',3.0),(32,'NVMe PCIe4 5.5GB/s',5.5)):
    for hit in (0.15,0.30,0.40):
        for bw in (45,25):
            r=run(f"RAM {ram} hit {hit:.2f} cpu {bw}GB/s ssd {ssd_lab}",2.31,12096,ram,free_q4,5.3,hit,bw,ssd)
            show(r)
    print()

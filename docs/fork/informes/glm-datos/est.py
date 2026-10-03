import math
# --- calibrate a Zipf-like skew on 288 experts so that top-100 covers 74% (neuralll LRU cache claim)
def cov(n,N,s): 
    return sum(i**-s for i in range(1,n+1))/sum(i**-s for i in range(1,N+1))
lo,hi=0,3
for _ in range(60):
    mid=(lo+hi)/2
    if cov(100,288,mid)<0.74: lo=mid
    else: hi=mid
s=lo
print("zipf s=",round(s,3),"top100->",round(cov(100,288,s),3))
for n in (8,12,16,20,30,40,60,100,144):
    print(f"  top-{n}/288 ({n/288*100:.1f}%) covers {cov(n,288,s)*100:.1f}%")
# Qwen anchor with same model: 512 experts, 18% (92 per layer) -> what s gives 0.50 (static) / 0.72 (adaptive)
for target,label in ((0.50,'static'),(0.72,'adaptive')):
    lo,hi=0,3
    for _ in range(60):
        mid=(lo+hi)/2
        if cov(92,512,mid)<target: lo=mid
        else: hi=mid
    print("Qwen",label,"s=",round(lo,3))

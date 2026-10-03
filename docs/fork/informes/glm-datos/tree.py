import json,sys,subprocess,collections
repo=sys.argv[1]
url=f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true&expand=true"
r=subprocess.run(['curl','-sS','-L',url],capture_output=True,text=True)
try: d=json.loads(r.stdout)
except Exception as e:
    print('ERR',r.stdout[:300]); sys.exit()
groups=collections.OrderedDict()
for f in d:
    if f.get('type')!='file': continue
    p=f['path']; sz=f.get('size',0)
    g=p.split('/')[0] if '/' in p else p
    if p.endswith('.gguf') or '/' in p:
        key=p.split('/')[0] if '/' in p else p
        groups.setdefault(key,[0,0]); groups[key][0]+=sz; groups[key][1]+=1
    else:
        groups.setdefault(p,[0,0]); groups[p][0]+=sz; groups[p][1]+=1
for k,(s,n) in groups.items(): print(f"{s/1e9:9.2f} GB  {n:3d} files  {k}")

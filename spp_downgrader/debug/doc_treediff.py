import h5py, struct, sys
# usage: python debug/doc_treediff.py <ref.spp> <built.spp> [dataset]
REF=sys.argv[1]   # known-good lower-version reference .spp
GOT=sys.argv[2]   # built .spp to compare against it
DS=sys.argv[3] if len(sys.argv)>3 else "paint/document.bin"
PRIM={1:4,2:8,3:12,4:16,5:4,6:8,7:12,8:16,9:8,0x0A:1,0x0B:4,0x0C:8,0x0D:36,0x0E:64,0x0F:8,0x15:8,16:32,21:8}

def parse(d):
    p=[12]
    def u8():v=d[p[0]];p[0]+=1;return v
    def u16():v=struct.unpack_from('<H',d,p[0])[0];p[0]+=2;return v
    def u32():v=struct.unpack_from('<I',d,p[0])[0];p[0]+=4;return v
    def obj():
        u8();u32();n=u32();name=d[p[0]:p[0]+n].decode('utf-8','replace');p[0]+=n;fc=u16()
        fields=[]
        for _ in range(fc):
            fl=u32();fn=d[p[0]:p[0]+fl].decode('utf-8','replace');p[0]+=fl
            fields.append((fn,val()))
        return {'t':name,'f':fields}
    def val():
        tag=u8()
        if tag==0x10:l=u32();s=d[p[0]:p[0]+l];p[0]+=l;return ('str',s.decode('utf-8','replace'))
        if tag in (0x12,0x14):
            if d[p[0]]==0xFF:p[0]+=1;return ('nullobj',None)
            return ('obj',obj())
        if tag in (0x13,0x11):
            u32();c=u32();els=[]
            for _ in range(c):
                e=u8()
                if e in (0x12,0x14):
                    if d[p[0]]==0xFF:p[0]+=1;els.append(('nullobj',None))
                    else:els.append(('obj',obj()))
                elif e==0x10:l=u32();s=d[p[0]:p[0]+l];p[0]+=l;els.append(('str',s.decode('utf-8','replace')))
                else:sz=PRIM[e];p[0]+=sz;els.append(('prim',e))
            return ('arr',els)
        if tag==0x00:return ('null',None)
        sz=PRIM[tag];p[0]+=sz;return ('prim',tag)
    assert u8()==0x12;return obj()

def load(path):
    f=h5py.File(path,'r');raw=bytes(f[DS][()]);f.close();return parse(raw)

def fnames(o): return [n for n,_ in o['f']]
def get(o,name):
    for n,v in o['f']:
        if n==name:return v
    return None

diffs=[]
def walk(r,g,path):
    # r,g are ('obj',objdict) or other value tuples
    rk,gk=r[0],g[0]
    if rk!=gk:
        diffs.append(f"KIND {path}: ref={rk} got={gk}")
        return
    if rk=='obj':
        ro,go=r[1],g[1]
        if ro['t']!=go['t']:
            diffs.append(f"TYPE {path}: ref={ro['t']} got={go['t']}")
        rf,gf=set(fnames(ro)),set(fnames(go))
        if rf!=gf:
            if rf-gf: diffs.append(f"MISSING-IN-GOT {path} ({ro['t']}): {sorted(rf-gf)}")
            if gf-rf: diffs.append(f"EXTRA-IN-GOT  {path} ({ro['t']}): {sorted(gf-rf)}")
        for n in fnames(ro):
            if n in gf:
                walk(get(ro,n),get(go,n),f"{path}/{n}")
    elif rk=='arr':
        ra,ga=r[1],g[1]
        if len(ra)!=len(ga):
            diffs.append(f"ARRAYLEN {path}: ref={len(ra)} got={len(ga)}")
        for i in range(min(len(ra),len(ga))):
            walk(ra[i],ga[i],f"{path}[{i}]")

r=load(REF); g=load(GOT)
walk(('obj',r),('obj',g),"")
print(f"=== {DS}: {len(diffs)} structural diffs ===")
for d in diffs: print(" ",d)

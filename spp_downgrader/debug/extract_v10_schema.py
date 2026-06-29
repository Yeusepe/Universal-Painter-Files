#!/usr/bin/env python3
"""Extract a v10 HBO schema (type -> ordered members) from a corpus of native
v10 .spp files, programmatically.

v10 tagged HBO encodes every object inline as: type name + (member name, value)*.
By walking many real v10 files we collect, per object type, the set/order of
members and the value-kind/type-code each member uses. This becomes the schema
the downgrader projects v11 objects onto (drop members v10 doesn't have, reorder
to v10 order) -- generic for any file.

Usage: python debug/extract_v10_schema.py [glob ...]  > schema.json
"""
import h5py, struct, sys, glob, json, os, collections

PRIM={1:4,2:8,3:12,4:16,5:4,6:8,7:12,8:16,9:8,0x0A:1,0x0B:4,0x0C:8,0x0D:36,0x0E:64,0x0F:8,0x15:8,16:32,21:8}
MAGIC=0x1B7C2FDD

# type_name -> {member_name -> Counter(type_code)} and order tracking
members = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
order_samples = collections.defaultdict(list)   # type -> list of member-name tuples (one per instance)

def parse(d):
    p=[12]
    def u8():v=d[p[0]];p[0]+=1;return v
    def u16():v=struct.unpack_from('<H',d,p[0])[0];p[0]+=2;return v
    def u32():v=struct.unpack_from('<I',d,p[0])[0];p[0]+=4;return v
    def obj():
        u8();u32();n=u32();name=d[p[0]:p[0]+n].decode('utf-8','replace');p[0]+=n;fc=u16()
        names=[]
        for _ in range(fc):
            fl=u32();fn=d[p[0]:p[0]+fl].decode('utf-8','replace');p[0]+=fl
            tag=d[p[0]]
            names.append(fn)
            members[name][fn][tag]+=1
            val()
        if names:
            order_samples[name].append(tuple(names))
    def val():
        tag=u8()
        if tag==0x10:l=u32();p[0]+=l
        elif tag in (0x12,0x14):
            if d[p[0]]==0xFF:p[0]+=1
            else:obj()
        elif tag in (0x13,0x11):
            u32();c=u32()
            for _ in range(c):
                e=u8()
                if e in (0x12,0x14):
                    if d[p[0]]==0xFF:p[0]+=1
                    else:obj()
                elif e==0x10:l=u32();p[0]+=l
                else:p[0]+=PRIM[e]
        elif tag==0x00:pass
        else:p[0]+=PRIM[tag]
    assert u8()==0x12;obj()

def canonical_order(samples):
    # union of members ordered by their median position across samples
    pos=collections.defaultdict(list)
    for s in samples:
        for i,n in enumerate(s): pos[n].append(i)
    return sorted(pos, key=lambda n: sorted(pos[n])[len(pos[n])//2])

def process(path):
    try: f=h5py.File(path,'r')
    except Exception: return 0
    n=0
    def vi(name,o):
        nonlocal n
        if isinstance(o,h5py.Dataset):
            try: raw=bytes(o[()])
            except Exception: return
            if len(raw)>=16 and struct.unpack('<I',raw[:4])[0]==MAGIC and struct.unpack('<I',raw[4:8])[0]==0:
                try: parse(raw); n+=1
                except Exception: pass
    f.visititems(vi); f.close(); return n

if __name__=="__main__":
    pats = sys.argv[1:] or [
        r"Original_Versions/*.spp", r"Simple PLane.spp",
        r"samples/*_v10*.spp", r"samples/Jammer_v10.spp",
        r"Textures_v10*.spp", r"v10 no dic.spp",
    ]
    files=[]
    for p in pats: files+=glob.glob(p)
    files=sorted(set(files))
    total=0
    for f in files:
        c=process(f); total+=c
        print(f"  parsed {c} v10 streams from {f}", file=sys.stderr)
    schema={}
    for t,mem in members.items():
        order=canonical_order(order_samples[t])
        schema[t]={m:{"codes":dict(mem[m]),"common_code":mem[m].most_common(1)[0][0]} for m in order}
    print(f"\n=== {len(schema)} v10 types from {len(files)} files / {total} streams ===", file=sys.stderr)
    for t in sorted(schema)[:60]:
        print(f"  {t}: {list(schema[t])}", file=sys.stderr)
    out=os.path.join(os.path.dirname(__file__),"v10_schema.json")
    json.dump(schema, open(out,"w"), indent=1)
    print(f"\nwrote {out}", file=sys.stderr)

#!/usr/bin/env python3
"""Parse the newest Substance Painter Crashpad minidump: exception, faulting
module/offset, registers, and a pseudo-backtrace of Painter.exe return addresses.

This replaces tailing Painter's log.txt, which gets null-truncated on a hard
crash. Crashpad writes a full minidump on every crash; this reads it post-mortem.

Usage: python debug/painter_crash.py [path-to.dmp]
       (no arg -> newest dump under the Crashpad reports dir)
Requires: pip install minidump
"""
import struct, os, sys, glob
from minidump.minidumpfile import MinidumpFile

REPORTS = os.path.expandvars(r"%LOCALAPPDATA%/Adobe/Adobe Substance 3D Painter/Crashpad/reports")

def newest_dump():
    dmps = glob.glob(os.path.join(REPORTS, "*.dmp"))
    return max(dmps, key=os.path.getmtime) if dmps else None

def main():
    dmp = sys.argv[1] if len(sys.argv) > 1 else newest_dump()
    if not dmp:
        print("no dump found in", REPORTS); return
    print("dump:", dmp)
    m = MinidumpFile.parse(dmp)
    raw = open(dmp, "rb").read()
    mods = [(int(mo.baseaddress), int(mo.size), os.path.basename(mo.name)) for mo in m.modules.modules]
    def modoff(a):
        for b, s, n in mods:
            if b <= a < b + s:
                return f"{n}+{hex(a-b)}"
        return hex(a)
    PB = PSZ = None
    for b, s, n in mods:
        if n.lower().startswith("adobe substance 3d painter.exe"):
            PB, PSZ = b, s

    fid = None
    if m.exception and m.exception.exception_records:
        er = m.exception.exception_records[0]
        fid = er.ThreadId
        fa = int(er.ExceptionRecord.ExceptionAddress)
        print("\nEXCEPTION:", er.ExceptionRecord.ExceptionCode, "at", hex(fa), "->", modoff(fa))

    t = next((x for x in m.threads.threads if x.ThreadId == fid), None)
    if t is None:
        print("no faulting thread"); return

    loc = t.ThreadContext  # CONTEXT (AMD64)
    ctx = raw[int(loc.Rva):int(loc.Rva) + int(loc.DataSize)]
    regs = {'Rax':0x78,'Rcx':0x80,'Rdx':0x88,'Rbx':0x90,'Rsp':0x98,'Rbp':0xA0,
            'Rsi':0xA8,'Rdi':0xB0,'R8':0xB8,'R9':0xC0,'R10':0xC8,'R11':0xD0,
            'R12':0xD8,'R13':0xE0,'R14':0xE8,'R15':0xF0,'Rip':0xF8}
    print("\nREGISTERS:")
    for r, o in regs.items():
        v = struct.unpack_from('<Q', ctx, o)[0]
        tag = f"  -> {modoff(v)}" if (PB and PB <= v < PB+PSZ) else ("  <- NULL/low" if v < 0x10000 else "")
        print(f"  {r:4}= {v:#018x}{tag}")

    ml = t.Stack.MemoryLocation
    data = raw[int(ml.Rva):int(ml.Rva) + int(ml.DataSize)]
    print("\nBACKTRACE (Painter.exe return addresses):")
    seen = set()
    for i in range(0, len(data) - 8, 8):
        v = struct.unpack_from('<Q', data, i)[0]
        if PB and PB <= v < PB + PSZ:
            off = v - PB
            if off not in seen:
                seen.add(off)
                print(f"  Painter.exe+{hex(off):>12}")
                if len(seen) >= 60:
                    break

if __name__ == "__main__":
    main()

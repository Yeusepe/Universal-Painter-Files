#!/usr/bin/env python3
"""Pre-warm the member-allowlist cache for every installed Painter version.

The downgrade engine drops any object member whose name is absent from the TARGET
version's binary (a member a version doesn't define has no string in its binary --
verified: 'changeFlags' added in v10, 'tilingMode' in v11.1). This is the authoritative,
programmatic alternative to hand-listing removed members per type: SOUND (a member the
target really has is in its binary, so never dropped) and complete for the common case.

The set is extracted ON DEMAND from your OWN installed Painter binaries and cached under
%LOCALAPPDATA%/USPP/member_cache -- nothing is committed to the repo or bundled in the
exe (so no Adobe-derived data is redistributed). This script just warms that cache up
front so the first conversion to a given version isn't slowed by the one-time extraction.

Usage:  python debug/extract_version_members.py
"""
import os, sys, glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "spp_extractor"))
from lib.hbo_reserializer import runtime


def main():
    base = os.environ.get("USPP_PAINTER_DIR") or r"C:\Program Files\Adobe"
    labels = []
    for d in sorted(glob.glob(os.path.join(base, "Adobe Substance 3D Painter v*"))):
        lab = os.path.basename(d).rsplit(" v", 1)[-1]
        if runtime._vkey(lab):
            labels.append(lab)
    if not labels:
        print("no installed Painter versions found under", base)
        return
    for lab in labels:
        members = runtime.load_members(lab)
        print(f"v{lab:6s} {'-> ' + str(len(members)) + ' identifiers cached' if members else 'NOT FOUND'}")


if __name__ == "__main__":
    main()

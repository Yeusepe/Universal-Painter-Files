"""Compatibility bridge for Painter's legacy per-object map exporter.

Painter's v8-era ``alg.mapexport.save`` can render layer UIDs, but an early
workflow check rejects UV-tile projects even though the underlying evaluator
and writer emit UDIM files correctly.  This module locates that check from a
stable string/xref signature, bypasses it only for the duration of capture,
and restores the original instruction immediately afterwards.
"""
import contextlib
import ctypes
import json
import os
import re
import struct


UV_TILE_ERROR = b"The map export is not available when using the UV Tile workflow"
_GUARD_CACHE = {}


def _pe_sections(data):
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("Painter executable is not a PE image")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("Painter executable has no PE signature")
    section_count = struct.unpack_from("<H", data, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    section_offset = pe_offset + 24 + optional_size
    sections = []
    for index in range(section_count):
        offset = section_offset + index * 40
        if offset + 40 > len(data):
            raise ValueError("Painter executable has a truncated section table")
        name = data[offset:offset + 8].rstrip(b"\0").decode("ascii", "replace")
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", data, offset + 8
        )
        sections.append({
            "name": name,
            "virtual_size": virtual_size,
            "virtual_address": virtual_address,
            "raw_size": raw_size,
            "raw_offset": raw_offset,
        })
    return sections


def _raw_to_rva(sections, raw_offset):
    for section in sections:
        start = section["raw_offset"]
        end = start + section["raw_size"]
        if start <= raw_offset < end:
            return section["virtual_address"] + raw_offset - start
    raise ValueError("file offset is outside mapped PE sections")


def _find_short_guard(text, ref_pos, ref_rva):
    preferred = ref_pos - 9
    candidates = [preferred]
    candidates.extend(range(ref_pos - 24, ref_pos - 1))
    seen = set()
    for pos in candidates:
        if pos in seen or pos < 0 or pos + 2 > len(text):
            continue
        seen.add(pos)
        if text[pos] != 0x74:  # JE rel8
            continue
        rel = struct.unpack_from("<b", text, pos + 1)[0]
        guard_rva = ref_rva + (pos - ref_pos)
        destination = guard_rva + 2 + rel
        if ref_rva < destination < ref_rva + 0x200:
            return guard_rva, bytes(text[pos:pos + 2])
    return None


def find_guard(executable):
    """Return ``(guard_rva, original_two_bytes)`` for a Painter executable."""
    path = os.path.abspath(executable)
    stat = os.stat(path)
    key = (path, stat.st_size, stat.st_mtime_ns)
    cached = _GUARD_CACHE.get(key)
    if cached is not None:
        return cached

    with open(path, "rb") as handle:
        data = handle.read()
    sections = _pe_sections(data)
    text_section = next((s for s in sections if s["name"] == ".text"), None)
    if text_section is None:
        raise ValueError("Painter executable has no .text section")
    text_start = text_section["raw_offset"]
    text = data[text_start:text_start + text_section["raw_size"]]

    string_offsets = []
    offset = 0
    while True:
        offset = data.find(UV_TILE_ERROR, offset)
        if offset < 0:
            break
        string_offsets.append(offset)
        offset += 1
    if not string_offsets:
        raise ValueError("Painter UV-tile mapexport guard string was not found")
    string_rvas = {_raw_to_rva(sections, offset) for offset in string_offsets}

    pos = 0
    while True:
        pos = text.find(b"\x8d", pos)
        if pos < 0:
            break
        if pos > 0 and 0x40 <= text[pos - 1] <= 0x4F and pos + 6 <= len(text):
            modrm = text[pos + 1]
            if modrm & 0xC7 == 0x05:
                instruction_pos = pos - 1
                instruction_rva = text_section["virtual_address"] + instruction_pos
                displacement = struct.unpack_from("<i", text, pos + 2)[0]
                target_rva = instruction_rva + 7 + displacement
                if target_rva in string_rvas:
                    guard = _find_short_guard(text, instruction_pos, instruction_rva)
                    if guard is not None:
                        _GUARD_CACHE.clear()
                        _GUARD_CACHE[key] = guard
                        return guard
        pos += 1
    raise ValueError("Painter UV-tile mapexport guard branch was not found")


@contextlib.contextmanager
def temporary_guard_bypass(executable, required=True):
    """Temporarily turn the guard's short JE into a short JMP in this process."""
    try:
        guard_rva, expected = find_guard(executable)
    except Exception:
        if required:
            raise
        yield False
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.VirtualProtect.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.VirtualProtect.restype = ctypes.c_int
    kernel32.FlushInstructionCache.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ]
    kernel32.FlushInstructionCache.restype = ctypes.c_int

    module_base = int(kernel32.GetModuleHandleW(None) or 0)
    if not module_base:
        raise RuntimeError("Could not find the running Painter module base")
    address = module_base + guard_rva
    actual = ctypes.string_at(address, len(expected))
    if actual != expected:
        raise RuntimeError(
            "Painter mapexport guard changed after discovery: expected {}, found {}".format(
                expected.hex(), actual.hex()
            )
        )

    old_protect = ctypes.c_ulong()
    if not kernel32.VirtualProtect(
            ctypes.c_void_p(address), 1, 0x40, ctypes.byref(old_protect)):
        raise ctypes.WinError(ctypes.get_last_error())
    process = kernel32.GetCurrentProcess()
    try:
        ctypes.memmove(address, b"\xEB", 1)
        kernel32.FlushInstructionCache(process, ctypes.c_void_p(address), 1)
        yield True
    finally:
        ctypes.memmove(address, expected[:1], 1)
        kernel32.FlushInstructionCache(process, ctypes.c_void_p(address), 1)
        restored = ctypes.c_ulong()
        kernel32.VirtualProtect(
            ctypes.c_void_p(address), 1, old_protect.value, ctypes.byref(restored)
        )


def expand_manifest_uv_tiles(manifest_path):
    """Replace a requested ``name.png`` asset with emitted ``name.1001.png`` tiles."""
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    base = os.path.dirname(os.path.abspath(manifest_path))
    expanded = []
    tile_count = 0
    for asset in manifest.get("assets") or []:
        relative = asset.get("path") or asset.get("file")
        if not relative:
            expanded.append(asset)
            continue
        absolute = relative if os.path.isabs(relative) else os.path.join(base, relative)
        folder = os.path.dirname(absolute)
        filename = os.path.basename(absolute)
        stem, extension = os.path.splitext(filename)
        pattern = re.compile(
            r"^" + re.escape(stem) + r"\.(1\d{3})" + re.escape(extension) + r"$",
            re.IGNORECASE,
        )
        matches = []
        if os.path.isdir(folder):
            for candidate in os.listdir(folder):
                match = pattern.match(candidate)
                if match:
                    matches.append((int(match.group(1)), candidate))
        if not matches:
            expanded.append(asset)
            continue
        for uv_tile, candidate in sorted(matches):
            item = dict(asset)
            item.pop("file", None)
            item["path"] = os.path.relpath(os.path.join(folder, candidate), base)
            item["uv_tile"] = uv_tile
            expanded.append(item)
            tile_count += 1
    manifest["assets"] = expanded
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return tile_count

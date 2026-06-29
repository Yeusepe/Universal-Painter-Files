#!/usr/bin/env python3
"""
Dict Remover Module - Remove dict entries from HBO binary streams.

This module provides functionality to:
1. Identify dicts to remove based on configurable rules
2. Safely remove dict entries from binary data
3. Validate result
4. Generate removal reports

Removal Strategy:
- Replace dicts with minimal stubs instead of deleting them
- This preserves structure and references
- Schema lookup will succeed (type name exists)
- Only content is removed
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import re
import struct
from .hbo_parser import DictEntry, find_all_dict_entries, parse_hbo_header


@dataclass
class RemovalRule:
    """Represents a single removal rule."""

    rule_type: str  # "blacklist", "whitelist", "regex"
    values: List[str]  # List of type names or patterns
    description: str = ""  # Optional description


@dataclass
class RemovalResult:
    """Result of a dict removal operation."""

    success: bool
    original_size: int
    new_size: int
    bytes_removed: int
    dicts_removed: List[DictEntry]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"RemovalResult(success={self.success}, "
            f"removed={len(self.dicts_removed)} dicts, "
            f"bytes_removed={self.bytes_removed})"
        )


def stub_dict_entry(entry: DictEntry, target_size: Optional[int] = None) -> bytes:
    """
    Create a minimal stub dict entry that preserves structure but removes content.

    IMPROVEMENT: To satisfy the v10 schema registry, we alias unknown types to "Object".
    Type code 0 (Null) is then used to ensure the parser doesn't expect any member data.
    """
    # ALIASING: Rename to "Object" which we know exists in v10
    # This prevents "[TRANSLATION] Invalid dict type"
    alias_name = "Object"
    name_bytes = alias_name.encode("ascii")
    name_length = len(name_bytes)

    # Use type code 0 (Null/Primitive) to stop the parser from reading members
    # In sub_1403909A0, type code 0 returns an empty object immediately.
    stub_type_code = 0

    # Stub header: length prefix + name + type code
    stub_header = struct.pack("<I", name_length)
    stub_header += name_bytes
    stub_header += struct.pack("<I", stub_type_code)
    header_size = len(stub_header)

    if target_size is not None and target_size > header_size:
        # Pad with zeros to preserve EXACT original size
        # This prevents shifting of subsequent dict entries and desyncing
        padding_size = target_size - header_size
        stub = stub_header + b"\x00" * padding_size
    else:
        # Fallback: just use minimal content if no target size
        stub = stub_header + struct.pack("<I", 0)

    return stub


def should_remove_blacklist(entry: DictEntry, blacklist: List[str]) -> bool:
    """Check if entry should be removed based on blacklist."""
    return entry.type_name in blacklist


def should_remove_whitelist(entry: DictEntry, whitelist: List[str]) -> bool:
    """Check if entry should be removed based on whitelist (keep only whitelist items)."""
    return entry.type_name not in whitelist


def should_remove_regex(entry: DictEntry, patterns: List[str]) -> bool:
    """Check if entry should be removed based on regex patterns."""
    for pattern in patterns:
        try:
            if re.match(pattern, entry.type_name):
                return True
        except re.error:
            continue
    return False


def identify_dicts_to_remove(
    entries: List[DictEntry], rule: RemovalRule
) -> List[DictEntry]:
    """
    Identify which dict entries should be removed based on a rule.

    Args:
        entries: List of DictEntry objects
        rule: RemovalRule specifying what to remove

    Returns:
        List of DictEntry objects to remove
    """
    to_remove = []

    for entry in entries:
        should_remove = False

        if rule.rule_type == "blacklist":
            should_remove = should_remove_blacklist(entry, rule.values)
        elif rule.rule_type == "whitelist":
            should_remove = should_remove_whitelist(entry, rule.values)
        elif rule.rule_type == "regex":
            should_remove = should_remove_regex(entry, rule.values)

        if should_remove:
            to_remove.append(entry)

    return to_remove


def identify_dicts_by_rules(
    entries: List[DictEntry], rules: List[RemovalRule], mode: str = "OR"
) -> List[DictEntry]:
    """
    Identify dicts to remove based on multiple rules.

    Args:
        entries: List of DictEntry objects
        rules: List of RemovalRule objects
        mode: "OR" (match any rule) or "AND" (match all rules)

    Returns:
        List of DictEntry objects to remove
    """
    if not rules:
        return []

    if mode == "OR":
        to_remove = set()
        for rule in rules:
            for entry in identify_dicts_to_remove(entries, rule):
                to_remove.add(entry.length_prefix_offset)

        return [e for e in entries if e.length_prefix_offset in to_remove]

    elif mode == "AND":
        # Entry must match ALL rules
        to_remove = []
        for entry in entries:
            matches_all = True
            for rule in rules:
                rule_matches = identify_dicts_to_remove([entry], rule)
                if not rule_matches:
                    matches_all = False
                    break
            if matches_all:
                to_remove.append(entry)
        return to_remove

    return []


def remove_dict_entry(data: bytes, entry: DictEntry, use_stub: bool = True) -> bytes:
    """
    Replace a single dict entry with a stub (or remove if use_stub=False).

    CRITICAL: When stubbing, we must preserve the EXACT original size to avoid
    shifting subsequent dict entries and breaking references. The stub fills the
    removed content with zeros instead of reducing the size.

    Args:
        data: Original binary data
        entry: DictEntry to replace
        use_stub: If True, replace with stub; if False, delete entirely

    Returns:
        New binary data with dict entry replaced
    """
    start = entry.length_prefix_offset
    end = entry.estimated_end_offset
    original_size = end - start

    if use_stub:
        # Create minimal stub header (unchanged: length prefix + name + type code)
        # Pass original_size to preserve exact dict size
        stub = stub_dict_entry(entry, target_size=original_size)

        # Replace original dict with same-sized stub
        new_data = data[:start] + stub + data[end:]
    else:
        # Create new data without dict entry (DEPRECATED - causes dangling references)
        new_data = data[:start] + data[end:]

    return new_data


def remove_multiple_dicts(
    data: bytes,
    entries_to_remove: List[DictEntry],
    recalculate_boundaries: bool = True,
    use_stub: bool = True,
) -> Tuple[bytes, RemovalResult]:
    """
    Remove multiple dict entries from binary data using stub replacement.

    Args:
        data: Original binary data
        entries_to_remove: List of DictEntry objects to remove
        recalculate_boundaries: If True, recalculate boundaries after each removal
        use_stub: If True, replace with stubs (safer); if False, delete entirely

    Returns:
        Tuple of (new_data, RemovalResult)
    """
    result = RemovalResult(
        success=True,
        original_size=len(data),
        new_size=0,
        bytes_removed=0,
        dicts_removed=[],
        warnings=[],
        errors=[],
    )

    if not entries_to_remove:
        result.new_size = len(data)
        return data, result

    # Sort by offset in descending order (replace from end first)
    sorted_entries = sorted(
        entries_to_remove, key=lambda e: e.length_prefix_offset, reverse=True
    )

    current_data = bytearray(data)

    for entry in sorted_entries:
        try:
            start = entry.length_prefix_offset
            end = entry.estimated_end_offset

            # Validate offsets
            if start < 12:  # Don't modify header
                result.warnings.append(
                    f"Skipping {entry.type_name}: offset {start} is in header"
                )
                continue

            if start >= len(current_data):
                result.warnings.append(
                    f"Skipping {entry.type_name}: offset {start} is out of bounds"
                )
                continue

            if end > len(current_data):
                end = len(current_data)
                result.warnings.append(
                    f"{entry.type_name}: adjusted end offset to {end}"
                )

            # Calculate original size and replacement size
            original_size = end - start

            if use_stub:
                # Create stub that preserves original exact size
                stub = stub_dict_entry(entry, target_size=original_size)
                # Replace with stub
                current_data[start : start + original_size] = stub
            else:
                # Delete entirely (DEPRECATED - causes dangling references)
                del current_data[start:end]

            # Track bytes removed (zeroed out content data)
            if use_stub:
                # When stubbing with same size, we count content bytes as removed
                name_bytes = entry.type_name.encode("ascii")
                header_size = 4 + len(name_bytes) + 4  # len + name + type_code
                bytes_removed = original_size - header_size
            else:
                # When deleting, full dict size is removed
                bytes_removed = original_size

            result.bytes_removed += bytes_removed
            result.dicts_removed.append(entry)

        except Exception as e:
            result.errors.append(f"Error removing {entry.type_name}: {str(e)}")
            result.success = False

    result.new_size = len(current_data)

    return bytes(current_data), result


def validate_removal(original_data: bytes, new_data: bytes) -> Tuple[bool, List[str]]:
    """
    Validate that removal was successful.

    Args:
        original_data: Original binary data
        new_data: Data after removal

    Returns:
        Tuple of (is_valid, list of issues)
    """
    issues = []

    # Check that we still have a valid header
    header = parse_hbo_header(new_data)
    if not header:
        issues.append("Invalid HBO header after removal")
        return False, issues

    # Check that header matches original
    original_header = parse_hbo_header(original_data)
    if original_header:
        if header.magic != original_header.magic:
            issues.append("Magic number changed")
        if header.version_check != original_header.version_check:
            issues.append("Version check changed")
        if header.data_version != original_header.data_version:
            issues.append("Data version changed")

    # Check that data is smaller
    if len(new_data) >= len(original_data):
        issues.append("Data did not shrink after removal")

    return len(issues) == 0, issues


def print_removal_report(result: RemovalResult, verbose: bool = True) -> None:
    """Print a summary of removal operation."""
    print("\n" + "=" * 60)
    print("DICT REMOVAL REPORT")
    print("=" * 60)

    print(f"\nStatus: {'SUCCESS' if result.success else 'FAILED'}")
    print(f"Original size: {result.original_size:,} bytes")
    print(f"New size: {result.new_size:,} bytes")
    print(f"Bytes removed: {result.bytes_removed:,} bytes")
    print(f"Dicts removed: {len(result.dicts_removed)}")

    if verbose and result.dicts_removed:
        print("\nRemoved dicts:")
        for entry in result.dicts_removed:
            print(f"  - {entry.type_name} (at 0x{entry.length_prefix_offset:X})")

    if result.warnings:
        print("\nWarnings:")
        for warning in result.warnings:
            print(f"  ! {warning}")

    if result.errors:
        print("\nErrors:")
        for error in result.errors:
            print(f"  X {error}")

    print("=" * 60)


def dry_run_removal(
    data: bytes,
    entries_to_remove: List[DictEntry],
    dataset_name: str = "",
    use_stub: bool = True,
) -> RemovalResult:
    """
    Simulate a removal operation without modifying data.

    Args:
        data: Binary data
        entries_to_remove: List of DictEntry objects to remove
        dataset_name: Name of dataset (for reporting)
        use_stub: If True, simulate stub replacement

    Returns:
        RemovalResult with simulated results
    """
    result = RemovalResult(
        success=True,
        original_size=len(data),
        new_size=0,
        bytes_removed=0,
        dicts_removed=[],
        warnings=[],
        errors=[],
    )

    # Sort by offset
    sorted_entries = sorted(entries_to_remove, key=lambda e: e.length_prefix_offset)

    total_bytes = 0
    for entry in sorted_entries:
        start = entry.length_prefix_offset
        end = entry.estimated_end_offset

        if start < 12:
            result.warnings.append(f"Would skip {entry.type_name}: offset in header")
            continue

        if start >= len(data):
            result.warnings.append(
                f"Would skip {entry.type_name}: offset out of bounds"
            )
            continue

        bytes_count = min(end, len(data)) - start
        if use_stub:
            # Stub is ~12-16 bytes depending on type name length
            stub = stub_dict_entry(entry)
            stub_size = len(stub)
            total_bytes += bytes_count - stub_size
        else:
            total_bytes += bytes_count

        result.dicts_removed.append(entry)

    result.bytes_removed = total_bytes
    result.new_size = result.original_size - total_bytes

    return result


# Convenience functions for common removal patterns


def remove_by_blacklist(
    data: bytes, blacklist: List[str], use_stub: bool = True
) -> Tuple[bytes, RemovalResult]:
    """Remove dicts that are in blacklist."""
    entries = find_all_dict_entries(data)
    rule = RemovalRule("blacklist", blacklist)
    to_remove = identify_dicts_to_remove(entries, rule)
    return remove_multiple_dicts(data, to_remove, use_stub=use_stub)


def remove_by_whitelist(
    data: bytes, whitelist: List[str], use_stub: bool = True
) -> Tuple[bytes, RemovalResult]:
    """Remove dicts that are NOT in whitelist (keep only whitelist)."""
    entries = find_all_dict_entries(data)
    rule = RemovalRule("whitelist", whitelist)
    to_remove = identify_dicts_to_remove(entries, rule)
    return remove_multiple_dicts(data, to_remove, use_stub=use_stub)


def remove_by_regex(
    data: bytes, patterns: List[str], use_stub: bool = True
) -> Tuple[bytes, RemovalResult]:
    """Remove dicts matching any of regex patterns."""
    entries = find_all_dict_entries(data)
    rule = RemovalRule("regex", patterns)
    to_remove = identify_dicts_to_remove(entries, rule)
    return remove_multiple_dicts(data, to_remove, use_stub=use_stub)

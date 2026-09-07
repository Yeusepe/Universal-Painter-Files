#!/usr/bin/env python3
"""
HBO Parser Module - Parse HellHeaven Base Objects binary format.

This module provides functionality to parse HBO binary streams found in .spp files,
identify dict entries, and track their boundaries for potential removal.

HBO Binary Format:
- Header (12 bytes):
  - Magic: 0x1B7C2FDD (4 bytes, little-endian)
  - Version check: 4 bytes (must be 0 for older Painter versions)
  - Data version: 4 bytes
- Payload:
  - Type byte (determines handler)
  - Object graph with length-prefixed dict type names

Dict Entry Format:
  [4 bytes: length of dict type name]
  [N bytes: dict type name (ASCII)]
  [4 bytes: type/code field]
  [... fields with nested structures ...]
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import struct
import re

# HBO Magic number
BINARY_MAGIC = 0x1B7C2FDD


@dataclass
class HBOHeader:
    """Represents the 12-byte HBO header."""
    magic: int
    version_check: int
    data_version: int

    @property
    def is_valid(self) -> bool:
        return self.magic == BINARY_MAGIC


@dataclass
class DictEntry:
    """Represents a dict entry found in HBO binary data."""
    length_prefix_offset: int   # Offset of 4-byte length field
    type_name: str              # Dict type name (e.g., "BakingCommonParameters")
    name_offset: int            # Offset where name starts
    name_length: int            # Length of type name
    type_code: int              # Type/code field after name
    type_code_offset: int       # Offset of type/code field
    estimated_end_offset: int   # Estimated end (to next entry or reasonable bound)
    dataset_name: str = ""      # Which dataset this came from

    def __repr__(self) -> str:
        return f"DictEntry('{self.type_name}' at 0x{self.length_prefix_offset:X})"


def parse_hbo_header(data: bytes) -> Optional[HBOHeader]:
    """
    Parse the 12-byte HBO header.

    Args:
        data: Raw binary data (at least 12 bytes)

    Returns:
        HBOHeader if valid, None otherwise
    """
    if len(data) < 12:
        return None

    magic = struct.unpack('<I', data[0:4])[0]
    version_check = struct.unpack('<I', data[4:8])[0]
    data_version = struct.unpack('<I', data[8:12])[0]

    header = HBOHeader(magic, version_check, data_version)
    return header if header.is_valid else None


def is_valid_dict_name(name: str) -> bool:
    """
    Check if a string is a valid dict type name.

    Valid dict names:
    - Start with uppercase letter
    - Contain only alphanumeric characters
    - Are between 3 and 64 characters long
    - Follow PascalCase convention
    """
    if not name or len(name) < 3 or len(name) > 64:
        return False

    if not name[0].isupper():
        return False

    if not name.isalnum():
        return False

    # Must have at least some lowercase (PascalCase, not ALLCAPS single word)
    # Unless it's a known constant pattern
    if name.isupper() and len(name) > 10:
        return False

    return True


def find_length_prefixed_strings(data: bytes, start_offset: int = 12) -> List[Tuple[int, str, int]]:
    """
    Find all length-prefixed ASCII strings in the data.

    Args:
        data: Raw binary data
        start_offset: Offset to start searching (default: after 12-byte header)

    Returns:
        List of (length_prefix_offset, string, type_code) tuples
    """
    results = []
    offset = start_offset

    while offset < len(data) - 8:  # Need at least 8 bytes for length + minimal string
        # Read potential length value
        length = struct.unpack('<I', data[offset:offset + 4])[0]

        # Check if length is reasonable for a dict type name
        if 3 <= length <= 64:
            string_start = offset + 4
            string_end = string_start + length

            if string_end + 4 <= len(data):  # Need 4 more bytes for type code
                try:
                    string_data = data[string_start:string_end]

                    # Check if all bytes are valid ASCII alphanumeric
                    if all(48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 for b in string_data):
                        string_value = string_data.decode('ascii')

                        if is_valid_dict_name(string_value):
                            # Read type code after the string
                            type_code = struct.unpack('<I', data[string_end:string_end + 4])[0]
                            results.append((offset, string_value, type_code))
                except:
                    pass

        offset += 1

    return results


def find_all_dict_entries(data: bytes, dataset_name: str = "") -> List[DictEntry]:
    """
    Find all dict entries in HBO binary data.

    Args:
        data: Raw binary data
        dataset_name: Name of the dataset (for reference)

    Returns:
        List of DictEntry objects, sorted by offset
    """
    header = parse_hbo_header(data)
    if not header:
        return []

    # Find all length-prefixed strings
    raw_entries = find_length_prefixed_strings(data, start_offset=12)

    # Convert to DictEntry objects
    entries = []
    for length_prefix_offset, type_name, type_code in raw_entries:
        name_offset = length_prefix_offset + 4
        name_length = len(type_name)
        type_code_offset = name_offset + name_length

        entry = DictEntry(
            length_prefix_offset=length_prefix_offset,
            type_name=type_name,
            name_offset=name_offset,
            name_length=name_length,
            type_code=type_code,
            type_code_offset=type_code_offset,
            estimated_end_offset=0,  # Will be calculated
            dataset_name=dataset_name
        )
        entries.append(entry)

    # Sort by offset
    entries.sort(key=lambda e: e.length_prefix_offset)

    # Calculate estimated end offsets
    for i, entry in enumerate(entries):
        if i + 1 < len(entries):
            # End is just before the next entry
            entry.estimated_end_offset = entries[i + 1].length_prefix_offset
        else:
            # Last entry - end is end of data
            # IMPROVEMENT: Don't assume it goes to the end if there's a 0-terminator or padding
            entry.estimated_end_offset = len(data)

    return entries

def refine_dict_boundaries(data: bytes, entries: List[DictEntry]) -> List[DictEntry]:
    """
    Refine dict boundaries by looking for common HBO end markers or
    valid structures after the expected data.
    """
    for entry in entries:
        # If the type_code is 0 (Null/Empty), it has no data after the 4-byte type_code
        if entry.type_code == 0:
            entry.estimated_end_offset = entry.type_code_offset + 4

    return entries

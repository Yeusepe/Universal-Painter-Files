#!/usr/bin/env python3
"""
HBO Encoder - Re-encode structured data back to HBO binary format.

This module is the inverse of the HBO decoder, taking decoded JSON/dict
data and converting it back to HBO v10 (tagged) or v11 (registry) format.

Usage:
    from hbo_encoder import HBOEncoder

    encoder = HBOEncoder()
    hbo_bytes = encoder.encode_v10(decoded_data, data_version=20)
"""

import struct
from typing import Dict, List, Any, Tuple

# HBO Constants
BINARY_MAGIC = 0x1B7C2FDD
BINARY_MAGIC_V11 = 0x69000B11

# Type codes
TYPE_NULL = 0
TYPE_UINT = 1
TYPE_FLOAT = 2
TYPE_BOOL = 3
TYPE_INT = 4
TYPE_FLOAT2 = 5
TYPE_FLOAT3 = 6
TYPE_FLOAT4 = 7
TYPE_INT2 = 8
TYPE_U8 = 9
TYPE_S32 = 10
TYPE_U32 = 11
TYPE_S64 = 12
TYPE_MATRIX3 = 13
TYPE_MATRIX4 = 14
TYPE_F32 = 15
TYPE_STRING = 16
TYPE_OBJECT = 18
TYPE_ARRAY = 19
TYPE_F64 = 21


class HBOEncoder:
    """Encode structured data to HBO binary format."""

    def __init__(self):
        self.buffer = bytearray()
        self.type_registry = {}
        self.type_code_counter = 0x10000  # Start type codes high to avoid conflicts

    def encode_v10(self, data: Dict[str, Any], data_version: int = 20) -> bytes:
        """
        Encode data to v10 (tagged) HBO format.

        Args:
            data: Dictionary containing typed entries
            data_version: HBO data version number

        Returns:
            bytes: HBO encoded binary data
        """
        self.buffer = bytearray()

        # Write header
        self.buffer.extend(struct.pack('<I', BINARY_MAGIC))
        self.buffer.extend(struct.pack('<I', 0))  # version_check = 0 for v10
        self.buffer.extend(struct.pack('<I', data_version))

        # For v10, each entry has: [name_length][name][type_code][payload]
        if isinstance(data, dict):
            for key, value in data.items():
                if key.startswith('_'):
                    continue  # Skip metadata keys
                self._write_v10_entry(key, value)

        return bytes(self.buffer)

    def encode_v11(self, data: Dict[str, Any], data_version: int = 20) -> bytes:
        """
        Encode data to v11 (registry-based) HBO format.

        Args:
            data: Dictionary containing typed entries
            data_version: HBO data version number

        Returns:
            bytes: HBO encoded binary data
        """
        self.buffer = bytearray()
        self.type_registry = {}

        # First pass: collect all type names
        self._collect_types(data)

        # Write header
        self.buffer.extend(struct.pack('<I', BINARY_MAGIC))
        self.buffer.extend(struct.pack('<I', 1))  # version_check = 1 for v11
        self.buffer.extend(struct.pack('<I', data_version))

        # Extended header (16 bytes of zeros typically)
        self.buffer.extend(b'\x00' * 16)

        # Write type registry
        for type_name, type_code in self.type_registry.items():
            self._write_string(type_name)
            self.buffer.extend(struct.pack('<I', type_code))

        # Write terminator
        self.buffer.extend(b'\xff\xff\xff\xff')

        # Write type definitions
        self._write_type_definitions(data)

        # Write data section
        self._write_v11_data(data)

        return bytes(self.buffer)

    def encode_v11_binary(self, data: Dict[str, Any]) -> bytes:
        """
        Encode data to v11 binary HBO format used by Painter's serializer.

        Expected data:
          - objects: list of raw object payload bytes
          - text_entries: list of raw byte blobs
        """
        self.buffer = bytearray()

        objects = data.get('objects', [])
        text_entries = data.get('text_entries', [])

        # 4-byte magic
        self.buffer.extend(struct.pack('<I', BINARY_MAGIC_V11))
        # object count
        self.buffer.extend(struct.pack('<I', len(objects)))

        # object payloads
        for obj in objects:
            self.buffer.append(0)  # per-object prefix
            self.buffer.extend(obj)

        # trailing text entries
        for entry in text_entries:
            if not entry:
                continue
            self.buffer.append(0)
            self.buffer.extend(struct.pack('<I', len(entry)))
            self.buffer.extend(entry)

        return bytes(self.buffer)

    def _write_v10_entry(self, type_name: str, value: Any):
        """Write a v10 tagged entry."""
        # PascalCase type names only
        if not type_name[0].isupper():
            return

        # Write name length and name
        name_bytes = type_name.encode('ascii')
        self.buffer.extend(struct.pack('<I', len(name_bytes)))
        self.buffer.extend(name_bytes)

        # Determine type code based on value
        type_code = self._infer_type_code(value)
        self.buffer.extend(struct.pack('<I', type_code))

        # Write payload
        self._write_value(value, type_code)

    def _write_value(self, value: Any, type_code: int):
        """Write a value based on its type code."""
        if value is None:
            return  # NULL has no payload

        if type_code == TYPE_BOOL:
            self.buffer.append(1 if value else 0)
        elif type_code in (TYPE_INT, TYPE_S32, TYPE_UINT, TYPE_U32):
            self.buffer.extend(struct.pack('<i' if type_code == TYPE_INT else '<I', int(value)))
        elif type_code in (TYPE_FLOAT, TYPE_F32):
            self.buffer.extend(struct.pack('<f', float(value)))
        elif type_code == TYPE_F64:
            self.buffer.extend(struct.pack('<d', float(value)))
        elif type_code == TYPE_STRING:
            self._write_string(str(value))
        elif type_code == TYPE_FLOAT2:
            for i in range(2):
                self.buffer.extend(struct.pack('<f', float(value[i]) if i < len(value) else 0.0))
        elif type_code == TYPE_FLOAT3:
            for i in range(3):
                self.buffer.extend(struct.pack('<f', float(value[i]) if i < len(value) else 0.0))
        elif type_code == TYPE_FLOAT4:
            for i in range(4):
                self.buffer.extend(struct.pack('<f', float(value[i]) if i < len(value) else 0.0))
        elif type_code == TYPE_INT2:
            for i in range(2):
                self.buffer.extend(struct.pack('<i', int(value[i]) if i < len(value) else 0))
        elif type_code == TYPE_ARRAY:
            self._write_array(value)
        elif type_code == TYPE_OBJECT:
            self._write_object(value)

    def _write_string(self, s: str):
        """Write a length-prefixed string."""
        encoded = s.encode('utf-8')
        self.buffer.extend(struct.pack('<I', len(encoded)))
        self.buffer.extend(encoded)

    def _write_array(self, arr: List[Any]):
        """Write an array."""
        if not arr:
            # Empty array
            self.buffer.extend(struct.pack('<I', TYPE_NULL))  # element type
            self.buffer.extend(struct.pack('<I', 0))  # count
            return

        # Determine element type
        elem_type = self._infer_type_code(arr[0])
        self.buffer.extend(struct.pack('<I', elem_type))
        self.buffer.extend(struct.pack('<I', len(arr)))

        for elem in arr:
            self._write_value(elem, elem_type)

    def _write_object(self, obj: Dict[str, Any]):
        """Write an object with fields."""
        if not isinstance(obj, dict):
            return

        # Write field count
        fields = [(k, v) for k, v in obj.items() if not k.startswith('_')]
        self.buffer.extend(struct.pack('<I', len(fields)))

        for name, value in fields:
            self._write_string(name)
            type_code = self._infer_type_code(value)
            self.buffer.extend(struct.pack('<I', type_code))
            self._write_value(value, type_code)

    def _infer_type_code(self, value: Any) -> int:
        """Infer HBO type code from Python value."""
        if value is None:
            return TYPE_NULL
        if isinstance(value, bool):
            return TYPE_BOOL
        if isinstance(value, int):
            return TYPE_INT
        if isinstance(value, float):
            return TYPE_FLOAT
        if isinstance(value, str):
            return TYPE_STRING
        if isinstance(value, (list, tuple)):
            if len(value) == 2 and all(isinstance(x, (int, float)) for x in value):
                if all(isinstance(x, int) for x in value):
                    return TYPE_INT2
                return TYPE_FLOAT2
            if len(value) == 3 and all(isinstance(x, (int, float)) for x in value):
                return TYPE_FLOAT3
            if len(value) == 4 and all(isinstance(x, (int, float)) for x in value):
                return TYPE_FLOAT4
            return TYPE_ARRAY
        if isinstance(value, dict):
            return TYPE_OBJECT
        return TYPE_NULL

    def _collect_types(self, data: Any, collected: set = None):
        """Collect all type names for v11 registry."""
        if collected is None:
            collected = set()

        if isinstance(data, dict):
            for key, value in data.items():
                if key[0].isupper() and key not in collected:
                    collected.add(key)
                    if key not in self.type_registry:
                        self.type_registry[key] = self.type_code_counter
                        self.type_code_counter += 1
                self._collect_types(value, collected)
        elif isinstance(data, list):
            for item in data:
                self._collect_types(item, collected)

    def _write_type_definitions(self, data: Any):
        """Write v11 type definitions section."""
        # For each registered type, write its definition
        for type_name in self.type_registry:
            self.buffer.extend(b'\xff\xff\xff\xff')  # Marker
            self._write_string(type_name)

            # Find an instance to get members
            members = self._find_type_members(data, type_name)
            self.buffer.extend(struct.pack('<I', len(members)))

            for member_name, member_type in members:
                self._write_string(member_name)
                self.buffer.extend(struct.pack('<I', member_type))

    def _find_type_members(self, data: Any, type_name: str) -> List[Tuple[str, int]]:
        """Find members of a type by scanning the data."""
        members = []

        if isinstance(data, dict):
            if type_name in data and isinstance(data[type_name], dict):
                for key, value in data[type_name].items():
                    if not key.startswith('_'):
                        members.append((key, self._infer_type_code(value)))
            for value in data.values():
                if not members:
                    members = self._find_type_members(value, type_name)
        elif isinstance(data, list):
            for item in data:
                if not members:
                    members = self._find_type_members(item, type_name)

        return members

    def _write_v11_data(self, data: Any):
        """Write v11 data section (after type registry)."""
        if isinstance(data, dict):
            # Count objects
            object_count = sum(1 for k in data if k[0].isupper())
            self.buffer.extend(struct.pack('<I', object_count))

            for key, value in data.items():
                if key.startswith('_'):
                    continue
                if key[0].isupper():
                    type_code = self.type_registry.get(key, TYPE_OBJECT)
                    self._write_v11_object(value, type_code)

    def _write_v11_object(self, obj: Any, type_code: int):
        """Write a v11 object using compact format."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key.startswith('_'):
                    continue
                child_type = self._infer_type_code(value)
                self._write_value(value, child_type)


def encode_to_hbo(data: Dict[str, Any], version: str = 'v10', data_version: int = 20) -> bytes:
    """
    Convenience function to encode data to HBO format.

    Args:
        data: Dictionary containing typed entries
        version: 'v10' or 'v11'
        data_version: HBO data version number

    Returns:
        bytes: HBO encoded binary data
    """
    encoder = HBOEncoder()
    if version == 'v11':
        return encoder.encode_v11(data, data_version)
    if version == 'v11_binary':
        return encoder.encode_v11_binary(data)
    return encoder.encode_v10(data, data_version)


if __name__ == '__main__':
    # Test encoding
    test_data = {
        'TestObject': {
            'name': 'Hello',
            'value': 42,
            'enabled': True,
            'position': [1.0, 2.0, 3.0]
        }
    }

    print("Testing HBO v10 encoder...")
    hbo_v10 = encode_to_hbo(test_data, 'v10', 20)
    print(f"  Generated {len(hbo_v10)} bytes")
    print(f"  Header: {hbo_v10[:12].hex()}")

    print("\nTesting HBO v11 encoder...")
    hbo_v11 = encode_to_hbo(test_data, 'v11', 20)
    print(f"  Generated {len(hbo_v11)} bytes")
    print(f"  Header: {hbo_v11[:12].hex()}")

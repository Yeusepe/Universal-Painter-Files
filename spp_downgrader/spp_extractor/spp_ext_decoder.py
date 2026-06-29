"""Standalone HBO stream decoder (used by the extractor when decode_hbo=True)."""
import struct
from typing import Dict, Any
from spp_ext_models import HBOHeader


class HBODecoder:
    """Decode HBO binary streams to structured data."""

    def __init__(self, data: bytes):
        self.data = data
        self.header = HBOHeader.from_bytes(data)
        if not self.header:
            raise ValueError("Invalid HBO header")

        # For v11, parse the type registry
        self.type_map = {}
        self.code_to_name = {}
        self.data_start = 12

        if self.header.version_check == 1:
            self._parse_v11_registry()

    def _parse_v11_registry(self):
        """Parse the v11 type registry section."""
        # v11 has a registry section after the header
        # Format: [type_name_length][type_name][type_code] repeated
        # Then FFFFFFFF marker, then definitions

        curr = 12
        if len(self.data) >= 28:
            # Skip extended header area, look for names
            curr = 28
            while curr + 8 <= len(self.data):
                # Check for terminator
                if self.data[curr:curr+4] == b'\xff\xff\xff\xff':
                    break

                # Read name length
                name_len = struct.unpack('<I', self.data[curr:curr+4])[0]
                if name_len > 256 or curr + 8 + name_len > len(self.data):
                    break

                # Read name and type code
                name = self.data[curr+4:curr+4+name_len].decode('utf-8', errors='replace')
                type_code = struct.unpack('<I', self.data[curr+4+name_len:curr+8+name_len])[0]

                self.code_to_name[type_code] = name
                curr += 8 + name_len

        # Find definition blocks (preceded by FFFFFFFF)
        pos = self.data.find(b'\xff\xff\xff\xff', 12)
        while pos != -1 and pos + 8 <= len(self.data):
            curr_def = pos + 4
            if curr_def + 8 > len(self.data):
                break

            name_len = struct.unpack('<I', self.data[curr_def:curr_def+4])[0]
            if name_len > 256 or curr_def + 4 + name_len + 4 > len(self.data):
                pos = self.data.find(b'\xff\xff\xff\xff', pos + 4)
                continue

            name = self.data[curr_def+4:curr_def+4+name_len].decode('utf-8', errors='replace')
            member_count = struct.unpack('<I', self.data[curr_def+4+name_len:curr_def+8+name_len])[0]

            if member_count > 1000:  # Sanity check
                pos = self.data.find(b'\xff\xff\xff\xff', pos + 4)
                continue

            # Parse members
            members = []
            ptr = curr_def + 8 + name_len
            valid = True

            for _ in range(member_count):
                if ptr + 8 > len(self.data):
                    valid = False
                    break

                ml = struct.unpack('<I', self.data[ptr:ptr+4])[0]
                if ml > 256 or ptr + 8 + ml > len(self.data):
                    valid = False
                    break

                mn = self.data[ptr+4:ptr+4+ml].decode('utf-8', errors='replace')
                mt = struct.unpack('<I', self.data[ptr+4+ml:ptr+8+ml])[0]
                members.append({'name': mn, 'type_code': mt})
                ptr += 8 + ml

            if valid:
                self.type_map[name] = {
                    'name': name,
                    'members': members
                }
                self.data_start = ptr

            pos = self.data.find(b'\xff\xff\xff\xff', ptr if valid else pos + 4)

    def decode(self) -> Dict[str, Any]:
        """Decode the HBO stream to a structured dictionary."""
        result = {
            'header': self.header.to_dict(),
            'type_registry': {
                'code_to_name': {str(k): v for k, v in self.code_to_name.items()},
                'type_definitions': self.type_map
            },
            'data': {}
        }

        if self.header.version_check == 1:
            # v11 registry-based format
            result['data'] = self._decode_v11_data()
        else:
            # v10 tagged format
            result['data'] = self._decode_v10_data()

        return result

    def _decode_v10_data(self) -> Dict[str, Any]:
        """Decode v10 tagged format data."""
        data = {}
        offset = 12

        # v10 uses tagged format: each entry has [length][name][type_code][payload]
        while offset < len(self.data) - 8:
            # Look for length-prefixed type names
            try:
                name_len = struct.unpack('<I', self.data[offset:offset+4])[0]
                if name_len < 3 or name_len > 64:
                    offset += 1
                    continue

                if offset + 4 + name_len + 4 > len(self.data):
                    break

                name_bytes = self.data[offset+4:offset+4+name_len]
                if not all(48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 for b in name_bytes):
                    offset += 1
                    continue

                name = name_bytes.decode('ascii')
                if not name[0].isupper() or not name.isalnum():
                    offset += 1
                    continue

                type_code = struct.unpack('<I', self.data[offset+4+name_len:offset+8+name_len])[0]

                data[name] = {
                    'offset': offset,
                    'type_code': type_code,
                    'entry_header_size': 4 + name_len + 4
                }

                offset += 4 + name_len + 4

                # Skip payload (we'll improve this with proper parsing)
                # For now, just record the position

            except Exception:
                offset += 1

        return data

    def _decode_v11_data(self) -> Dict[str, Any]:
        """Decode v11 registry-based format data."""
        data = {}

        if self.data_start >= len(self.data):
            return data

        # The actual data starts after the registry
        # Format: [count][objects...]
        try:
            offset = self.data_start
            if offset + 4 > len(self.data):
                return data

            count = struct.unpack('<I', self.data[offset:offset+4])[0]
            data['_object_count'] = count

            # Would need to recursively decode objects here
            # For now, just report the structure
            data['_data_start'] = self.data_start
            data['_remaining_bytes'] = len(self.data) - self.data_start

        except Exception as e:
            data['_decode_error'] = str(e)

        return data

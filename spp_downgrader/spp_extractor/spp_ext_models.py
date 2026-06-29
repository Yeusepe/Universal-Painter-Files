"""HBO header/dataset value types and constants for the extractor."""
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Any


try:
    from lib.hbo_parser import BINARY_MAGIC
except ImportError:
    print("Warning: lib modules not found, using embedded HBO parser")
    BINARY_MAGIC = 0x1B7C2FDD

BINARY_MAGIC_V11 = 0x69000B11


# ============================================================================
# HBO Types and Constants
# ============================================================================

# HBO Type Codes
HBO_TYPE_NULL = 0
HBO_TYPE_UINT = 1      # unsigned int (TFieldDefinition_Impl<I>)
HBO_TYPE_FLOAT = 2     # float (TFieldDefinition_Impl<M>)
HBO_TYPE_BOOL = 3      # bool (TFieldDefinition_Impl<_N>)
HBO_TYPE_INT = 4       # signed int (TFieldDefinition_Impl<H>)
HBO_TYPE_FLOAT2 = 5    # TVector<M,2>
HBO_TYPE_FLOAT3 = 6    # TVector<M,3>
HBO_TYPE_FLOAT4 = 7    # TVector<M,4>
HBO_TYPE_INT2 = 8      # TVector<H,2>
HBO_TYPE_STRING = 16   # CString
HBO_TYPE_OBJECT = 18   # Nested object / dict
HBO_TYPE_ARRAY = 19    # TArray<T>

# Primitive sizes in bytes
HBO_PRIMITIVE_SIZES = {
    1: 4,   # unsigned int
    2: 4,   # float
    3: 1,   # bool (sometimes 4, check actual data)
    4: 4,   # signed int
    5: 8,   # float2
    6: 12,  # float3
    7: 16,  # float4
    8: 8,   # int2
    9: 1,   # u8
    10: 4,  # s32
    11: 4,  # u32
    12: 8,  # s64/u64
    13: 36, # matrix3x3 or similar
    14: 64, # matrix4x4
    15: 4,  # f32
    21: 8,  # f64
}


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class HBOHeader:
    """Represents the 12-byte HBO header."""
    magic: int
    version_check: int  # 0 = v10 (tagged), 1 = v11 (registry-based)
    data_version: int

    @property
    def is_valid(self) -> bool:
        return self.magic == BINARY_MAGIC

    @property
    def format_version(self) -> str:
        """Returns 'v10' for tagged format, 'v11' for registry format."""
        if self.version_check == 0:
            return "v10"
        elif self.version_check == 1:
            return "v11"
        else:
            return f"unknown({self.version_check})"

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> Optional['HBOHeader']:
        if len(data) < offset + 12:
            return None
        magic, version_check, data_version = struct.unpack(
            '<III', data[offset:offset+12]
        )
        header = cls(magic, version_check, data_version)
        return header if header.is_valid else None

    def to_dict(self) -> dict:
        return {
            'magic': f'0x{self.magic:08X}',
            'version_check': self.version_check,
            'data_version': self.data_version,
            'format': self.format_version
        }


@dataclass
class HBOV11BinaryHeader:
    """Represents the v11 binary HBO header."""
    magic: int
    object_count: int

    @property
    def is_valid(self) -> bool:
        return self.magic == BINARY_MAGIC_V11

    def to_dict(self) -> dict:
        return {
            'magic': f'0x{self.magic:08X}',
            'format': 'v11_binary',
            'object_count': self.object_count,
        }

@dataclass
class ExtractedDataset:
    """Represents an extracted HDF5 dataset."""
    path: str
    size: int
    dtype: str
    is_hbo: bool
    hbo_header: Optional[HBOHeader]
    attributes: Dict[str, Any]
    attributes_dtypes: Dict[str, str]
    attributes_orders: Dict[str, int]
    data: bytes  # Raw data, or decoded if applicable
    decoded: Optional[Any] = None  # Decoded representation
    creation_props: Optional[Dict[str, Any]] = None  # HDF5 creation properties


@dataclass
class ExtractedGroup:
    """Represents an extracted HDF5 group."""
    path: str
    creation_props: Dict[str, Any]
    attributes: Dict[str, Any]
    datasets: List[str]
    subgroups: List[str]


@dataclass
class SPPExtraction:
    """Complete extraction result from an SPP file."""
    source_file: str
    extraction_time: str
    hdf5_structure: Dict[str, Any]
    groups: Dict[str, ExtractedGroup]
    datasets: Dict[str, ExtractedDataset]
    metadata: Dict[str, Any]
    errors: List[str]

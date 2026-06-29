#!/usr/bin/env python3
"""
Type code mapping between v11 and v10 formats

Based on analysis of actual files, most type codes follow the pattern:
v10_code = (high_bits << 16) | v11_code
"""

# Mapping table: type_name -> (high_bits, v11_code) or direct v10_code
# For entries that follow the pattern: (high_bits, v11_code)
# For entries that don't: use direct v10_code value
TYPE_CODE_MAPPINGS = {
    # Pattern: (high_bits << 16) | v11_code
    'Data2DTransformation': (8, 8),
    'DataActionFilterLevels': (12, 7),
    'DataActionGroup': (12, 8),
    'DataBitmap': (9, 4),
    'DataBlending': (12, 3),
    'DataColorSpaceOverride': (4, 4),
    'DataGridDeformation': (5, 4),
    'DataGridVertex': (10, 6),
    'DataLayerColor': (7, 17),
    'DataLayerGroup': (17, 19),
    'DataLayersPreset': (10, 4),
    'DataLevels': (5, 7),
    'DataProcedural': (22, 7),
    'DataProceduralInput': (11, 7),
    'DataProceduralInputsSource': (5, 4),
    'DataProceduralOutput': (12, 7),
    'DataSourceBitmap': (6, 4),
    'DataSourceUniform': (12, 4),
    'DataStackActions': (5, 2),
    'DataStackLayers': (5, 2),
    'DataTweakFloat': (10, 3),
    'DataTweakInt': (10, 3),
    'DataTweakInt2': (10, 3),
    'GUIPivot': (0, 2),  # Same code
    'GUIcollapsedState': (7, 10),

    # Direct mappings (don't follow simple pattern)
    'Aluminum': 11,
    'Contrast': 3,
    'DataActionFill': 983067,
    'DataSourceProcedural': 786441,
    'GUIScaleRatioLocked': 2227978,
    'Level': 3,
    'Opacity': 7,

    # v11-only entries (will need special handling)
    'DataResolutionOverride': None,  # v11 only
    'DataSymmetry': None,  # v11 only
}


def map_type_code_v11_to_v10(type_name: str, v11_code: int) -> int:
    """
    Map v11 type code to v10 type code.

    Args:
        type_name: Name of the dict entry type
        v11_code: Type code from v11 format

    Returns:
        Type code for v10 format
    """
    mapping = TYPE_CODE_MAPPINGS.get(type_name)

    if mapping is None:
        # Unknown entry type - try to infer from v11 code
        # Assume it might follow the pattern with common high bits
        # This is a fallback - ideally all entries should be in the mapping table
        return v11_code

    if isinstance(mapping, tuple):
        # Follows pattern: (high_bits << 16) | v11_code
        high_bits, expected_v11_code = mapping
        if v11_code != expected_v11_code:
            # Warn if v11 code doesn't match expected
            # This might indicate the mapping needs updating
            pass  # Could add logging here
        return (high_bits << 16) | v11_code
    else:
        # Direct mapping
        return mapping


def map_type_code_v10_to_v11(type_name: str, v10_code: int) -> int:
    """
    Map v10 type code to v11 type code (reverse mapping).

    Args:
        type_name: Name of the dict entry type
        v10_code: Type code from v10 format

    Returns:
        Type code for v11 format
    """
    mapping = TYPE_CODE_MAPPINGS.get(type_name)

    if mapping is None:
        # Unknown - extract low 16 bits (common pattern)
        return v10_code & 0xFFFF

    if isinstance(mapping, tuple):
        # Extract low 16 bits (v11 code)
        high_bits, v11_code = mapping
        # Verify high bits match
        if (v10_code >> 16) == high_bits:
            return v11_code
        else:
            # High bits don't match - extract anyway
            return v10_code & 0xFFFF
    else:
        # Direct mapping - need to reverse lookup
        # This is tricky - for now, extract low bits
        return v10_code & 0xFFFF

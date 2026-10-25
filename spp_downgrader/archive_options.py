"""Resolve CLI archive names and validate LZMA2 options without allocating an encoder."""
from pathlib import Path
import re

from lib.project_archive import CompressionSettings


_INTEGER_LIMITS = {
    'preset': (0, 9), 'nice_len': (2, 273), 'depth': (0, 0xFFFFFFFF),
    'lc': (0, 4), 'lp': (0, 4), 'pb': (0, 4),
}
_CHOICES = {'mode': {'fast', 'normal'}, 'mf': {'hc3', 'hc4', 'bt2', 'bt3', 'bt4'}}
_KEYS = set(_INTEGER_LIMITS) | set(_CHOICES) | {'extreme', 'dict_size'}


def parse_advanced_config(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('--advanced-config needs comma-separated key=value settings')
    values = {}
    for item in text.split(','):
        key, separator, raw = item.strip().partition('=')
        key, raw = key.strip(), raw.strip()
        if not separator or not raw:
            raise ValueError(f'invalid advanced setting: {item!r}; use key=value')
        if key not in _KEYS:
            raise ValueError(f'unsupported LZMA2 option {key!r}; supported keys: {", ".join(sorted(_KEYS))}')
        if key in values:
            raise ValueError(f'duplicate advanced setting: {key}')
        if key in _INTEGER_LIMITS:
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f'{key} must be an integer') from None
            low, high = _INTEGER_LIMITS[key]
            if not low <= value <= high:
                raise ValueError(f'{key} must be between {low} and {high}')
        elif key == 'dict_size':
            match = re.fullmatch(r'([0-9]+)\s*(B|KiB|MiB|GiB)?', raw, re.IGNORECASE)
            if not match:
                raise ValueError('dict_size needs integer bytes or a KiB, MiB, or GiB suffix')
            factor = {None: 1, 'b': 1, 'kib': 1024, 'mib': 1024 ** 2, 'gib': 1024 ** 3}
            unit = match[2].lower() if match[2] else None
            value = int(match[1]) * factor[unit]
            if not 4096 <= value <= 1536 * 1024 ** 2:
                raise ValueError('dict_size must be between 4 KiB and 1536 MiB')
        elif key == 'extreme':
            if raw.lower() not in ('true', 'false'):
                raise ValueError('extreme must be true or false')
            value = raw.lower() == 'true'
        else:
            value = raw.lower()
            if value not in _CHOICES[key]:
                raise ValueError(f'{key} must be one of: {", ".join(sorted(_CHOICES[key]))}')
        values[key] = value
    if values.get('lc', 3) + values.get('lp', 0) > 4:
        raise ValueError('lc + lp must be at most 4')
    return values


def resolve_compression(method=None, output=None, advanced_config=None):
    extension_method = None
    if output is not None:
        extension = Path(output).suffix.lower()
        extension_method = {'.uspp': 'deflate', '.usppz': 'lzma2'}.get(extension)
        if extension_method is None:
            raise ValueError('archive output must end in .uspp or .usppz')
    if method is not None and method not in ('deflate', 'lzma2'):
        raise ValueError(f'unknown compression method: {method}')
    if method and extension_method and method != extension_method:
        raise ValueError(f'Compression method {method} conflicts with the output extension')
    method = method or extension_method or 'deflate'
    if advanced_config is not None and method != 'lzma2':
        raise ValueError('--advanced-config requires LZMA2 compression and .usppz output')
    values = parse_advanced_config(advanced_config) if advanced_config is not None else {}
    return CompressionSettings(method, preset=values.pop('preset', 6),
                               extreme=values.pop('extreme', False), overrides=tuple(sorted(values.items())))


def describe_compression(settings):
    if settings.method == 'deflate':
        return 'Compression: Deflate level 1 (.uspp, ZIP)'
    options = [f'preset={settings.preset}', f'extreme={str(settings.extreme).lower()}']
    options.extend(f'{key}={value}' for key, value in settings.overrides)
    return 'Compression: LZMA2 (.usppz, 7z), ' + ', '.join(options)

"""Archive containers for Universal SPP project data.

The engine defaults to ZIP. CLI callers choose compression before creating paths.
7z reads use numbered, regular cache files instead of extracting archive paths.
"""
from contextlib import nullcontext
from dataclasses import dataclass
import json
import lzma
from pathlib import Path
import stat
import tempfile
import zipfile


PROJECT_METADATA_MEMBERS = ('manifest.json', 'metadata.json', 'structure.json',
                            'groups.json', 'datasets.json', 'raster/manifest.json')


@dataclass(frozen=True)
class CompressionSettings:
    method: str = 'deflate'
    preset: int = 6
    extreme: bool = False
    overrides: tuple = ()

    @property
    def extension(self):
        return '.usppz' if self.method == 'lzma2' else '.uspp'

    def metadata(self):
        if self.method == 'deflate':
            return {'container': 'zip', 'method': 'deflate', 'level': 1}
        return {'container': '7z', 'method': 'lzma2', 'preset': self.preset,
                'extreme': self.extreme, 'overrides': dict(self.overrides)}

    def filters(self):
        options = dict(self.overrides)
        if 'mode' in options:
            options['mode'] = {'fast': lzma.MODE_FAST, 'normal': lzma.MODE_NORMAL}[options['mode']]
        if 'mf' in options:
            options['mf'] = getattr(lzma, 'MF_' + options['mf'].upper())
        return [{'id': lzma.FILTER_LZMA2,
                 'preset': self.preset | (lzma.PRESET_EXTREME if self.extreme else 0), **options}]


def _member_key(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError(f'unsafe archive member: {name!r}')
    parts = name.rstrip('/').split('/')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in parts):
        raise ValueError(f'unsafe archive member: {name!r}')
    # Archive members are case-sensitive. Numeric cache paths preserve that on Windows.
    return '/'.join(parts)


class ArchiveWriter:
    def __init__(self, path, settings=None):
        self.settings = settings or CompressionSettings()
        self._names = set()
        if self.settings.method == 'deflate':
            self._archive = zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=1)
        elif self.settings.method == 'lzma2':
            import py7zr
            self._archive = py7zr.SevenZipFile(path, 'w', filters=self.settings.filters())
        else:
            raise ValueError(f'unknown compression method: {self.settings.method}')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._archive.close()

    def _claim(self, name):
        key = _member_key(name)
        if key in self._names:
            raise ValueError(f'archive contains duplicate entries: {name}')
        self._names.add(key)

    def writestr(self, name, data):
        self._claim(name)
        if self.settings.method == 'deflate':
            self._archive.writestr(name, data)
        else:
            self._archive.writestr(data, name)

    def write(self, filename, arcname):
        self._claim(arcname)
        self._archive.write(filename, arcname)


class ArchiveReader:
    def __init__(self, path, members=None):
        self.path = Path(path)
        self._cache = None
        self._cached_files = None
        self._members = members
        self._all_crcs = False
        with self.path.open('rb') as source:
            signature = source.read(6)
        if signature == b"7z\xbc\xaf\x27\x1c":
            import py7zr
            self.container = '7z'
            self._archive = py7zr.SevenZipFile(self.path, 'r')
        elif zipfile.is_zipfile(self.path):
            self.container = 'zip'
            self._archive = zipfile.ZipFile(self.path, 'r')
        else:
            raise ValueError('not a ZIP or 7z project archive')
        try:
            self._validate_members()
        except BaseException:
            self.close()
            raise

    def _validate_members(self):
        seen = set()
        self._files = set()
        if self.container == 'zip':
            entries = self._archive.infolist()
            items = [(info.orig_filename, info.is_dir(),
                      stat.S_ISLNK(info.external_attr >> 16)) for info in entries]
        else:
            if self._archive.needs_password():
                raise ValueError('encrypted project archives are not supported')
            entries = self._archive.list()
            items = [(info.filename, info.is_directory,
                      info.is_symlink or not (info.is_file or info.is_directory)) for info in entries]
            self._all_crcs = all(info.crc32 is not None for info in entries if info.is_file)
        self._names = [item[0] for item in items]
        for name, is_directory, is_link in items:
            key = _member_key(name)
            if is_link:
                raise ValueError(f'archive links are not supported: {name}')
            if key in seen:
                raise ValueError(f'archive contains duplicate entries: {name}')
            seen.add(key)
            if not is_directory:
                self._files.add(name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self._archive.close()
        finally:
            if self._cache is not None:
                # All files under this directory have tool-controlled numeric names.
                if Path(self._cache.name).resolve() != self._cache_root:
                    raise RuntimeError('archive cache path changed; refusing cleanup')
                self._cache.cleanup()
                self._cache = None

    def namelist(self):
        return list(self._names)

    def compression_info(self):
        if self.container == '7z':
            methods = self._archive.archiveinfo().method_names
        else:
            labels = {zipfile.ZIP_STORED: 'stored', zipfile.ZIP_DEFLATED: 'deflate',
                      zipfile.ZIP_BZIP2: 'bzip2', zipfile.ZIP_LZMA: 'lzma'}
            methods = sorted({labels.get(info.compress_type, str(info.compress_type))
                              for info in self._archive.infolist() if not info.is_dir()})
        return {'container': self.container, 'methods': methods}

    def _extract_cache(self):
        from py7zr.io import Py7zIO, WriterFactory
        self._cache = tempfile.TemporaryDirectory(prefix='uspp-read-')
        self._cache_root = Path(self._cache.name).resolve()
        selected = self._files if self._members is None else self._files.intersection(self._members)
        paths = {name: self._cache_root / f'{index}.bin' for index, name in enumerate(sorted(selected))}

        class DiskIO(Py7zIO):
            def __init__(self, path):
                self.file = path.open('w+b')
                self._size = 0

            def write(self, data):
                count = self.file.write(data)
                self._size = max(self._size, self.file.tell())
                return count

            def read(self, size=None):
                return self.file.read(-1 if size is None else size)

            def seek(self, offset, whence=0):
                return self.file.seek(offset, whence)

            def flush(self):
                self.file.flush()

            def size(self):
                return self._size

            def close(self):
                self.file.close()

        class DiskFactory(WriterFactory):
            def __init__(self):
                self.outputs = []

            def create(self, filename):
                # No member name is ever used as a filesystem destination.
                output = DiskIO(paths[filename])
                self.outputs.append(output)
                return output

        factory = DiskFactory()
        try:
            self._archive.reset()
            self._archive.extract(targets=sorted(selected), factory=factory)
        finally:
            for output in factory.outputs:
                output.close()
        if any(not path.is_file() for path in paths.values()):
            raise ValueError('archive extraction did not produce all requested members')
        self._cached_files = paths

    def read(self, name):
        if name not in self._files:
            raise KeyError(name)
        if self.container == 'zip':
            return self._archive.read(name)
        if self._members is not None and name not in self._members:
            raise KeyError(f'member was not selected for this read: {name}')
        if self._cached_files is None:
            self._extract_cache()
        return self._cached_files[name].read_bytes()

    def testzip(self):
        if self.container == 'zip':
            return self._archive.testzip()
        # A complete factory extraction already checked every member CRC.
        if self._members is None and self._cached_files is not None and self._all_crcs:
            return None
        self._archive.reset()
        return self._archive.testzip()


def open_archive(source, members=None):
    if isinstance(source, ArchiveReader):
        return nullcontext(source)
    return ArchiveReader(source, members=members)


def verify_project_archive(archive, full=False):
    """Validate member references before an archive can replace its source."""
    def object_member(name):
        value = json.loads(archive.read(name))
        if not isinstance(value, dict):
            raise ValueError(f'invalid project metadata object: {name}')
        return value

    manifest = object_member('manifest.json')
    if manifest.get('errors'):
        raise ValueError('project extraction failed: ' + '; '.join(map(str, manifest['errors'])))
    metadata = object_member('metadata.json')
    object_member('structure.json')
    groups = object_member('groups.json')
    datasets = object_member('datasets.json')
    for key, actual in (('dataset_count', len(datasets)), ('group_count', len(groups))):
        if key in manifest and manifest[key] != actual:
            raise ValueError(f'project {key} does not match its index')
    members = archive._files
    references = set()
    for info in datasets.values():
        data_file = info.get('data_file')
        if not data_file or data_file not in members or data_file in references:
            raise ValueError(f'missing or reused dataset member: {data_file}')
        references.add(data_file)
        if info.get('decoded_file') and info['decoded_file'] not in members:
            raise ValueError(f'missing decoded member: {info["decoded_file"]}')
    if 'raster/manifest.json' in members:
        raster = object_member('raster/manifest.json')
        for asset in raster.get('assets') or []:
            if asset.get('archive_path') not in members:
                raise ValueError(f'missing raster member: {asset.get("archive_path")}')
    if full:
        version = manifest.get('created_version') or metadata.get('painter_version')
        if not isinstance(version, dict) or version.get('major') is None:
            raise ValueError('archive has no source Painter version; source was kept')
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f'archive integrity check failed: {bad_member}')
    return manifest

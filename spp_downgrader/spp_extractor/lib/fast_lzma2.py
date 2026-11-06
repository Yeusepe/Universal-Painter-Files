"""Streaming ctypes adapter for the pinned Fast LZMA2 ABI and py7zr 1.1.x.

Only per-archive plain LZMA2 codecs are replaced. The 7z container, checksums,
member order, solid stream, header handling, and extraction stay with py7zr.
"""
import ctypes as C
from functools import lru_cache
from pathlib import Path
import sys


class Buffer(C.Structure):
    _fields_ = [('data', C.c_void_p), ('size', C.c_size_t), ('pos', C.c_size_t)]


# FL2_cParameter values from the pinned upstream fast-lzma2.h.
LEVEL, HIGH, DICTIONARY = 0, 1, 3
FAST_LENGTH, LC, LP, PB, OMIT_PROPERTIES, XXHASH = 10, 13, 14, 15, 16, 17


@lru_cache(maxsize=1)
def library():
    filename = library_filename()
    if getattr(sys, 'frozen', False):
        path = Path(sys._MEIPASS) / 'native' / filename
    else:
        path = Path(__file__).resolve().parents[3] / 'native' / 'bin' / filename
    if not path.is_file():
        raise RuntimeError('Fast LZMA2 library is missing. Run python native/build.py or reinstall the portable bundle.')
    dll = C.CDLL(str(path))
    signatures = {
        'FL2_versionNumber': (C.c_uint, []),
        'FL2_versionString': (C.c_char_p, []),
        'FL2_createCStreamMt': (C.c_void_p, [C.c_uint, C.c_int]),
        'FL2_freeCStream': (None, [C.c_void_p]),
        'FL2_CStream_setParameter': (C.c_size_t, [C.c_void_p, C.c_int, C.c_size_t]),
        'FL2_CStream_getParameter': (C.c_size_t, [C.c_void_p, C.c_int]),
        'FL2_initCStream': (C.c_size_t, [C.c_void_p, C.c_int]),
        'FL2_getCCtxDictProp': (C.c_ubyte, [C.c_void_p]),
        'FL2_getCCtxThreadCount': (C.c_uint, [C.c_void_p]),
        'FL2_compressStream': (C.c_size_t, [C.c_void_p, C.POINTER(Buffer), C.POINTER(Buffer)]),
        'FL2_endStream': (C.c_size_t, [C.c_void_p, C.POINTER(Buffer)]),
        'FL2_isError': (C.c_uint, [C.c_size_t]),
        'FL2_getErrorName': (C.c_char_p, [C.c_size_t]),
        'FL2_createDStreamMt': (C.c_void_p, [C.c_uint]),
        'FL2_freeDStream': (C.c_size_t, [C.c_void_p]),
        'FL2_initDStream_withProp': (C.c_size_t, [C.c_void_p, C.c_ubyte]),
        'FL2_setDStreamMemoryLimitMt': (None, [C.c_void_p, C.c_size_t]),
        'FL2_decompressStream': (C.c_size_t, [C.c_void_p, C.POINTER(Buffer), C.POINTER(Buffer)]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(dll, name)
        function.restype, function.argtypes = result, arguments
    if dll.FL2_versionNumber() != 10001:
        raise RuntimeError('Unsupported Fast LZMA2 ABI; rebuild the pinned native dependency.')
    return dll


def library_filename(platform=None):
    platform = platform or sys.platform
    if platform == 'win32':
        return 'fast-lzma2.dll'
    if platform == 'darwin':
        return 'libfast-lzma2.dylib'
    if platform.startswith('linux'):
        return 'libfast-lzma2.so'
    raise RuntimeError(f'Unsupported native compression platform: {platform}')


class FastLzma2Encoder:
    def __init__(self, settings):
        self._stream = None
        self._dll = library()
        self._output = C.create_string_buffer(1024 * 1024)
        # The pinned upstream dual-buffer path can race while copying output and
        # leaks its partially initialized context when thread-pool creation fails.
        self._stream = self._dll.FL2_createCStreamMt(settings.effective_threads, 0)
        if not self._stream:
            raise MemoryError('Could not allocate Fast LZMA2 stream')
        try:
            self._set(HIGH, int(settings.extreme))
            self._set(LEVEL, max(1, settings.preset))
            overrides = dict(settings.overrides)
            for key, parameter in (('dict_size', DICTIONARY), ('nice_len', FAST_LENGTH),
                                   ('lc', LC), ('lp', LP), ('pb', PB)):
                if key in overrides:
                    self._set(parameter, overrides[key])
            # 7z stores the dictionary property in its coder header, not in the stream.
            self._set(OMIT_PROPERTIES, 1)
            self._set(XXHASH, 0)
            self._check(self._dll.FL2_initCStream(self._stream, 0))
            self.properties = bytes([self._dll.FL2_getCCtxDictProp(self._stream)])
            self.info = dict(encoder='fast-lzma2', encoder_version=self._dll.FL2_versionString().decode('ascii'),
                             encoder_level=self._get(LEVEL), dictionary_size=self._get(DICTIONARY),
                             threads=self._dll.FL2_getCCtxThreadCount(self._stream))
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if self._dll.FL2_isError(result):
            raise RuntimeError('Fast LZMA2: ' + self._dll.FL2_getErrorName(result).decode('utf-8'))
        return result

    def _set(self, parameter, value):
        return self._check(self._dll.FL2_CStream_setParameter(self._stream, parameter, value))

    def _get(self, parameter):
        return self._check(self._dll.FL2_CStream_getParameter(self._stream, parameter))

    def compress(self, data):
        if not self._stream:
            raise ValueError('Fast LZMA2 stream is closed')
        if not data:
            return b''
        if not isinstance(data, bytes):
            data = bytes(data)
        pointer = C.c_char_p(data)  # Holds input alive; the native stream copies consumed bytes.
        input_buffer = Buffer(C.cast(pointer, C.c_void_p), len(data), 0)
        chunks = []
        pending = True
        while input_buffer.pos < input_buffer.size or pending:
            output = Buffer(C.cast(self._output, C.c_void_p), len(self._output), 0)
            previous = input_buffer.pos
            pending = self._check(self._dll.FL2_compressStream(self._stream, C.byref(output), C.byref(input_buffer)))
            if output.pos:
                chunks.append(C.string_at(output.data, output.pos))
            if input_buffer.pos == previous and not output.pos and input_buffer.pos < input_buffer.size:
                raise RuntimeError('Fast LZMA2 made no input or output progress')
        return b''.join(chunks)

    def flush(self):
        if not self._stream:
            raise ValueError('Fast LZMA2 stream is closed')
        chunks = []
        try:
            while True:
                output = Buffer(C.cast(self._output, C.c_void_p), len(self._output), 0)
                pending = self._check(self._dll.FL2_endStream(self._stream, C.byref(output)))
                if output.pos:
                    chunks.append(C.string_at(output.data, output.pos))
                if not pending:
                    return b''.join(chunks)
        finally:
            self.close()

    def close(self):
        if self._stream:
            self._dll.FL2_freeCStream(self._stream)
            self._stream = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class FastLzma2Decoder:
    """Bounded streaming decoder with the interface used by py7zr's codec chain."""
    def __init__(self, properties, threads=8):
        self._stream = None
        self._pending = b''
        self.eof = False
        if len(properties) != 1 or properties[0] > 40:
            raise ValueError('Invalid LZMA2 dictionary property')
        self._dll = library()
        self._stream = self._dll.FL2_createDStreamMt(threads)
        if not self._stream:
            raise MemoryError('Could not allocate Fast LZMA2 decoder')
        try:
            # Eight 64 MiB reset blocks plus compressed input exceed the upstream
            # 512 MiB default, which silently falls back to single-thread decoding.
            # This bounds MT buffers; the dictionary and Python buffers are additional.
            self._dll.FL2_setDStreamMemoryLimitMt(self._stream, 1024 * 1024**2)
            self._check(self._dll.FL2_initDStream_withProp(self._stream, properties[0]))
        except BaseException:
            self.close()
            raise

    _check = FastLzma2Encoder._check
    __del__ = FastLzma2Encoder.__del__

    def decompress(self, data, max_length=-1):
        if self.eof:
            if data:
                raise EOFError('Data after LZMA2 stream end')
            return b''
        if not self._stream:
            raise ValueError('Fast LZMA2 decoder is closed')
        data = self._pending + bytes(data)
        pointer = C.c_char_p(data)
        source = Buffer(C.cast(pointer, C.c_void_p), len(data), 0)
        chunks = []
        remaining = max_length
        try:
            while remaining != 0:
                capacity = 1024**2 if remaining < 0 else min(1024**2, remaining)
                storage = C.create_string_buffer(capacity)
                output = Buffer(C.cast(storage, C.c_void_p), capacity, 0)
                previous = source.pos
                pending = self._check(self._dll.FL2_decompressStream(
                    self._stream, C.byref(output), C.byref(source)))
                if output.pos:
                    chunks.append(storage.raw[:output.pos])
                    if remaining > 0:
                        remaining -= output.pos
                if not pending:
                    self.eof = True
                    self.close()
                    if source.pos != source.size:
                        raise ValueError('Trailing data in LZMA2 stream')
                    break
                if source.pos == previous and not output.pos:
                    break  # More compressed input is required.
            self._pending = data[source.pos:]
            return b''.join(chunks)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._stream:
            self._dll.FL2_freeDStream(self._stream)
            self._stream = None


def install_decoders(archive):
    """Hook only this archive's plain LZMA2 folders; retain py7zr CRC handling."""
    import os
    streams = archive.header.main_streams
    decoders = []
    if streams is None:
        return decoders
    pack_index = 0
    for folder in streams.unpackinfo.folders:
        sizes = streams.packinfo.packsizes[pack_index:pack_index + len(folder.packed_indices)]
        pack_index += len(folder.packed_indices)
        if (len(folder.coders) != 1 or folder.coders[0]['method'] != b'\x21'
                or folder.coders[0]['numinstreams'] != 1
                or folder.coders[0]['numoutstreams'] != 1 or len(sizes) != 1):
            continue
        result = folder.get_decompressor(sizes[0])
        decoder = FastLzma2Decoder(folder.coders[0]['properties'], min(8, os.cpu_count() or 1))
        result.chain[0] = decoder
        result.block_size = 256 * 1024
        decoders.append(decoder)
    return decoders


def install_encoder(archive, settings):
    """Replace one fresh, per-archive compressor; never patch py7zr module globals."""
    encoder = FastLzma2Encoder(settings)
    try:
        folder = archive.header.initialize()
        compressor = folder.compressor
        if (len(compressor.chain) != 1 or len(compressor.coders) != 1 or
                compressor.coders[0]['method'] != b'\x21' or compressor._unpacksizes != [0]):
            raise RuntimeError('Unsupported py7zr compressor layout; requires py7zr 1.1.x')
        compressor.chain[0] = encoder
        compressor.coders[0]['properties'] = encoder.properties
        return encoder
    except BaseException:
        encoder.close()
        raise

from concurrent.futures import ThreadPoolExecutor
import lzma
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader'))
from archive_options import resolve_compression
from lib.fast_lzma2 import FastLzma2Encoder, FastLzma2Decoder
from lib.project_archive import ArchiveReader, ArchiveWriter, CompressionSettings


class FastLzma2Tests(unittest.TestCase):
    def test_decoder_fragmented_input_bounded_output_and_legacy_stream(self):
        payload = os.urandom(1024**2 + 123) + b'legacy data' * 10000
        compressed = lzma.compress(payload, format=lzma.FORMAT_RAW,
            filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': 1024**2}])
        decoder = FastLzma2Decoder(b'\x10', threads=2)
        self.addCleanup(decoder.close)
        chunks = []
        for index in range(0, len(compressed), 1777):
            chunks.append(decoder.decompress(compressed[index:index + 1777], 4093))
        while not decoder.eof:
            chunks.append(decoder.decompress(b'', 4093))
        self.assertTrue(all(len(chunk) <= 4093 for chunk in chunks))
        self.assertEqual(b''.join(chunks), payload)
        self.assertIsNone(decoder._stream)

    def test_native_reader_rejects_corrupt_payload_and_releases_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'corrupt.usppz'
            with ArchiveWriter(path, CompressionSettings('lzma2', threads=2)) as writer:
                writer.writestr('payload', os.urandom(1024**2))
            with path.open('r+b') as file:
                file.seek(100)
                value = file.read(1)
                file.seek(100)
                file.write(bytes([value[0] ^ 1]))
            reader = ArchiveReader(path)
            with self.assertRaises(Exception):
                with reader:
                    reader.read('payload')
            self.assertTrue(reader._decoders)
            self.assertTrue(all(decoder._stream is None for decoder in reader._decoders))

    def test_raw_stream_empty_tiny_and_multiple_dictionary_blocks(self):
        for data in (b'', b'hello', os.urandom(2 * 1024**2 + 177), b'abcd' * (1024**2)):
            with self.subTest(bytes=len(data)):
                settings = CompressionSettings('lzma2', threads=2, overrides=(('dict_size', 1024**2),))
                encoder = FastLzma2Encoder(settings)
                chunks = [encoder.compress(data[i:i + 65537]) for i in range(0, len(data), 65537)]
                chunks.append(encoder.flush())
                output = lzma.decompress(b''.join(chunks), format=lzma.FORMAT_RAW,
                    filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': encoder.info['dictionary_size']}])
                self.assertEqual(output, data)
                encoder.close()  # Idempotent cleanup after flush.

    def test_fast_settings_and_legacy_fallback_are_explicit(self):
        settings = resolve_compression('lzma2', advanced_config='threads=2,dict_size=1MiB,lc=2,lp=1,pb=1,nice_len=64')
        self.assertEqual(settings.encoder, 'fast-lzma2')
        self.assertEqual(settings.threads, 2)
        encoder = FastLzma2Encoder(settings)
        self.addCleanup(encoder.close)
        self.assertEqual(encoder.info['dictionary_size'], 1024**2)
        self.assertEqual(encoder.info['threads'], 2)
        payload = b'literal properties' * 1000
        compressed = encoder.compress(payload) + encoder.flush()
        self.assertEqual(lzma.decompress(compressed, format=lzma.FORMAT_RAW,
            filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': encoder.info['dictionary_size']}]), payload)
        for config in ('mf=bt4', 'mode=normal', 'depth=64', 'dict_size=64KiB', 'nice_len=2'):
            self.assertEqual(resolve_compression('lzma2', advanced_config=config).encoder, 'liblzma')
        with self.assertRaises(ValueError):
            resolve_compression('lzma2', advanced_config='threads=2,mf=bt4')

    def test_two_concurrent_archives_do_not_patch_py7zr_globals(self):
        import py7zr.compressor
        original = py7zr.compressor.LZMA1Compressor
        with tempfile.TemporaryDirectory() as directory:
            def create(index):
                path = Path(directory) / f'{index}.usppz'
                payload = bytes([index]) * (2 * 1024**2) + os.urandom(65536)
                settings = CompressionSettings('lzma2', threads=2)
                with ArchiveWriter(path, settings) as writer:
                    self.assertEqual(writer.compression_metadata()['encoder'], 'fast-lzma2')
                    writer.writestr('first.bin', payload)
                    writer.writestr('second.bin', payload[::-1])
                with ArchiveReader(path) as reader:
                    self.assertEqual(reader.read('first.bin'), payload)
                    self.assertEqual(reader.read('second.bin'), payload[::-1])
                    self.assertIsNone(reader.testzip())
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(create, [1, 2]))
        self.assertIs(py7zr.compressor.LZMA1Compressor, original)

    def test_extreme_and_preset_zero_emit_standard_streams(self):
        for preset, extreme in ((0, False), (1, False), (6, True)):
            settings = CompressionSettings('lzma2', preset=preset, extreme=extreme, threads=1)
            encoder = FastLzma2Encoder(settings)
            payload = b'configuration roundtrip' * 1000
            compressed = encoder.compress(payload) + encoder.flush()
            self.assertEqual(lzma.decompress(compressed, format=lzma.FORMAT_RAW,
                filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': encoder.info['dictionary_size']}]), payload)

    def test_native_context_is_released_on_archive_error(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = ArchiveWriter(Path(directory) / 'error.usppz', CompressionSettings('lzma2', threads=1))
            with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                with writer:
                    writer.writestr('data', b'data')
                    raise RuntimeError('deliberate')
            self.assertIsNone(writer._encoder._stream)

    def test_empty_archive_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'empty.usppz'
            with ArchiveWriter(path, CompressionSettings('lzma2', threads=1)):
                pass
            with ArchiveReader(path) as reader:
                self.assertEqual(reader.namelist(), [])
                self.assertIsNone(reader.testzip())

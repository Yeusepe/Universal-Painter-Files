"""Helpers for corrupting real archive payloads in safety tests."""
import json
import struct
import zipfile


def corrupt_zip_payload(path):
    with zipfile.ZipFile(path) as archive:
        index = json.loads(archive.read('datasets.json'))
        info = archive.getinfo(index['paint/payload.bin']['data_file'])
    with path.open('r+b') as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
        if header[:4] != b'PK\x03\x04' or not info.compress_size:
            raise ValueError('ZIP payload has no data to corrupt')
        name_length, extra_length = struct.unpack_from('<HH', header, 26)
        stream.seek(info.header_offset + 30 + name_length + extra_length + info.compress_size // 2)
        original = stream.read(1)
        stream.seek(-1, 1)
        stream.write(bytes([original[0] ^ 1]))

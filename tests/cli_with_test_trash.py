"""Run the real CLI with disposal isolated from the user's Recycle Bin.

The top-level patch also runs when multiprocessing imports this entry point.
This file is test-only and is not bundled into the application.
"""
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'spp_downgrader'))
import uspp_tool


def test_recycle(source):
    root = Path(os.environ['USPP_TEST_TRASH_ROOT']).resolve()
    source = Path(source).absolute()
    if root not in source.parents:
        raise RuntimeError('Test disposal outside the fixture directory is forbidden')
    trash = root / '.test-trash'
    trash.mkdir(exist_ok=True)
    source.rename(trash / (uuid.uuid4().hex + '-' + source.name))


uspp_tool._recycle_source = test_recycle

if __name__ == '__main__':
    uspp_tool.multiprocessing.freeze_support()
    uspp_tool.main()

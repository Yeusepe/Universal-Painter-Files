"""Build the pinned Fast LZMA2 library for the current operating system."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'spp_downgrader' / 'spp_extractor'))
from lib.fast_lzma2 import library_filename


def build(compiler=None):
    default = r'C:\msys64\ucrt64\bin\gcc.exe' if sys.platform == 'win32' else 'cc'
    compiler = compiler or os.environ.get('CC') or default
    executable = shutil.which(compiler)
    if executable is None:
        raise RuntimeError(f'C compiler not found: {compiler}. Install GCC or the Xcode command line tools.')
    output = ROOT / 'bin' / library_filename()
    output.parent.mkdir(exist_ok=True)
    flags = ['-O3', '-DNDEBUG', '-DFL2_DLL_EXPORT=1']
    if sys.platform == 'win32':
        flags += ['-shared', '-static-libgcc']
    elif sys.platform == 'darwin':
        flags += ['-fPIC', '-pthread', '-dynamiclib', '-Wl,-install_name,@rpath/' + output.name]
    else:
        flags += ['-fPIC', '-pthread', '-shared', '-Wl,-soname,' + output.name]
    subprocess.run([executable, *flags, *map(str, sorted((ROOT / 'fast-lzma2').glob('*.c'))),
                    '-o', str(output)], check=True)
    print(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', help='C compiler executable; defaults to CC, then the platform compiler')
    args = parser.parse_args()
    build(args.compiler)

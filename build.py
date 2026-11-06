"""Build a native portable CLI and optionally stage and package the Painter plugin."""
import argparse
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'spp_downgrader'


def build(cli_only=False, compiler=None):
    updater = runpy.run_path(str(ROOT / 'universal_spp_plugin/lib/updater.py'))
    tag = updater['platform_tag']()
    command = [sys.executable, str(ROOT / 'native/build.py')]
    if compiler:
        command += ['--compiler', compiler]
    subprocess.run(command, check=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', 'uspp_tool.spec', '--noconfirm'],
                   cwd=ENGINE, check=True)
    bundle = ENGINE / 'dist/uspp_tool'
    for name in ('LICENSE', 'NOTICE.md', 'CLI.md'):
        shutil.copy2(ROOT / name, bundle)
    shutil.copy2(ROOT / 'native/THIRD_PARTY_NOTICES.txt', bundle / 'Fast-LZMA2-LICENSE.txt')
    filename = 'uspp_tool.exe' if sys.platform == 'win32' else 'uspp_tool'
    if not (bundle / filename).is_file():
        raise RuntimeError(f'Build did not produce {filename}')
    archive = ENGINE / 'dist' / ('uspp_tool-' + tag)
    if sys.platform == 'win32':
        archive = Path(shutil.make_archive(str(archive), 'zip', root_dir=bundle.parent, base_dir=bundle.name))
    else:
        archive = archive.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as output:
            output.add(bundle, arcname=bundle.name)
    print(f'CLI -> {archive}')
    if cli_only:
        return archive
    plugin = ROOT / 'universal_spp_plugin'
    target = plugin / 'bin/uspp_tool'
    # Replace only the generated converter inside this checkout's plugin folder.
    if target.is_symlink() or target.resolve().parent != (plugin / 'bin').resolve():
        raise RuntimeError(f'Refusing to replace converter outside plugin bin: {target}')
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    shutil.copytree(bundle, target, symlinks=True)
    legacy = plugin / 'bin/uspp_tool.exe'
    if legacy.exists():
        legacy.unlink()
    plugin_archive = ENGINE / 'dist' / updater['asset_name_for_version'](updater['PLUGIN_VERSION'])
    with tempfile.TemporaryDirectory(prefix='uspp-plugin-package-') as temporary:
        # Plugin ZIP updates use regular files. Dereference PyInstaller's internal
        # symlinks here; portable CLI tarballs keep the original symlinks and modes.
        stage = Path(temporary) / plugin.name
        shutil.copytree(plugin, stage, symlinks=False,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.gitkeep'))
        with zipfile.ZipFile(plugin_archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    output.write(path, path.relative_to(stage.parent))
    print(f'Plugin -> {plugin_archive}')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli-only', action='store_true', help='Do not stage or package the Painter plugin')
    parser.add_argument('--compiler', help='C compiler executable, passed to native/build.py')
    args = parser.parse_args()
    build(args.cli_only, args.compiler)

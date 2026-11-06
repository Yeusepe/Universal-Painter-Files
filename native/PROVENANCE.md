# Fast LZMA2

Source: https://github.com/conor42/fast-lzma2

Pinned revision: `967306d39daacf9a14ad923c86fa7f9c4552b59b`.
The upstream C and header files are unmodified. The project uses the upstream BSD license in `fast-lzma2/LICENSE`.
The upstream source is dual-licensed. This distribution selects the BSD option.
`THIRD_PARTY_NOTICES.txt` preserves the upstream license and source notices, including bundled component attributions, for binary distributions.

`build.ps1` builds a Windows x64 DLL with MSYS2 UCRT64 GCC. It uses the portable C implementation and Windows-native threads.
It does not use CPU-specific `-march=native` flags or require an external 7-Zip installation.

`python native/build.py` builds the native library on Windows, Linux, or macOS.
Linux uses GCC or Clang with POSIX threads and produces `libfast-lzma2.so`.
macOS uses the Xcode command line compiler and produces `libfast-lzma2.dylib` for the current Python architecture.
Windows produces `fast-lzma2.dll`. The `CC` environment variable or `--compiler` selects another compiler.

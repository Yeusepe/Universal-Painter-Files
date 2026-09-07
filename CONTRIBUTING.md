# Contributing

By contributing, you confirm that your contribution is your own original work or
is provided under a license that allows it to be included in this MIT-licensed
project.

## Tests

Run the Python suite and the capture companion's JavaScript behavior tests from
the repository root. The JavaScript tests use Node.js 18 or newer and its built-in
test runner; no npm packages or Painter installation are needed.

```text
python -m unittest discover -s tests -q
node --test tests/raster_capture.test.js
```

## Copyright rules

Do not contribute:

- Adobe source code, binaries, product icons, logos, screenshots, documentation,
  sample project files, or other proprietary Adobe material.
- Proprietary material from any other company or creator unless its license
  clearly permits redistribution here.
- Decompiled code, disassembly listings, translated vendor code, leaked source
  code, NDA material, private SDK material, or proprietary implementation notes.
- Files or instructions that bypass licensing, activation, DRM, encryption,
  subscriptions, entitlement checks, or access controls.
- `.spp`, `.uspp`, texture, mesh, material, font, or other asset files from a
  real project unless every included asset is yours or is clearly licensed for
  redistribution in this repository.

Allowed sources for contributions include:

- Public documentation that permits the use being made of it.
- Behavior observed by running lawfully obtained software.
- File-format facts learned from project files you own or have permission to
  inspect.
- Synthetic test files and fixtures created from scratch for this project.
- Third-party code or assets with a compatible license and attribution.

## Reverse-engineering notes

This project is for interoperability. Keep reverse-engineering notes factual:
field names, observed byte layouts, version numbers, error messages, and test
results are useful. Do not paste proprietary text, copied tables, source code,
decompiled output, or screenshots from proprietary tools.

If you have seen leaked source code, NDA material, or other restricted material
related to the code you want to touch, do not contribute to that area. Tell the
maintainers so the work can be handled by someone who is not exposed to the
restricted material.

## Issues and test files

Do not upload copyrighted project files to issues or pull requests. Prefer a
minimal synthetic file that reproduces the bug. If a real project is required,
share only through a private channel agreed by the maintainers and only if you
have permission to share every asset inside it.

# Third-party notices

Chattera is released under the MIT License (see [LICENSE](LICENSE)). It is built on **agentchattr**
(MIT, Copyright (c) 2026 Ben Curtis, https://github.com/bcurts/agentchattr); its copyright notice is kept in LICENSE.

## Included in this repository

| Component | Version | License | Location |
|---|---|---|---|
| marked | 18.2.0 | MIT | `src/static/mc/vendor/marked.umd.js` (license: `vendor/marked.LICENSE`) |
| KaTeX (incl. fonts) | 0.19.1 | MIT | `src/static/mc/vendor/katex/` (license: `katex/LICENSE`) |
| highlight.js | 11.12.0 | BSD-3-Clause | `src/static/mc/vendor/hljs/` (license: `hljs/LICENSE`) |

## Python packages (installed by `requirements.txt`; bundled into the portable package)

Direct dependencies: fastapi (MIT), uvicorn (BSD-3-Clause), mcp (MIT), httpx (BSD-3-Clause), python-multipart
(Apache-2.0), packaging (Apache-2.0 OR BSD-2-Clause), pypdf (BSD-3-Clause), pypdfium2 (Apache-2.0 OR BSD-3-Clause;
bundles PDFium, BSD-3-Clause, and its third-party libraries), python-docx (MIT), lxml (BSD-3-Clause; bundles libxml2
and libxslt, MIT), Pillow (MIT-CMU; bundles libjpeg-turbo, libpng, zlib, FreeType and others).

Their transitive dependencies include starlette, pydantic, pydantic-core, anyio, h11, httpcore, httptools, websockets,
watchfiles, python-dotenv, PyYAML, certifi (MPL-2.0; unmodified CA bundle, source at https://github.com/certifi/python-certifi),
cryptography, PyJWT, jsonschema, sse-starlette, pywin32 and others.

The portable package lists **every bundled package with its exact version and license** in `THIRD_PARTY_NOTICES.txt`
and ships each package's own license, NOTICE and bundled-library license files in the `licenses` folder. That list is
generated from the installed metadata by `scripts/collect_licenses.py` at build time, so it always matches the build.

## Runtime of the portable package

- **Python** (interpreter and standard library) — Python Software Foundation License; text in `licenses/Python`.
- **PyInstaller bootloader** — GPL-2.0-or-later with the PyInstaller bootloader exception, which permits distributing
  the resulting executable under any license.

## Text recognition (OCR) — included in the portable package

- rapidocr-onnxruntime (Apache-2.0, with its bundled PaddleOCR-derived models) with onnxruntime (MIT), OpenCV
  (opencv-python, Apache-2.0), NumPy (BSD-3-Clause and others), Shapely (BSD-3-Clause), pyclipper (MIT), PyYAML (MIT),
  six (MIT) — from `requirements-ocr.txt`. Optional when running from source.

## Development only

- pytest (MIT), PyInstaller (GPL-2.0 with bootloader exception), playwright-core (Apache-2.0, for
  rendering the user guides).

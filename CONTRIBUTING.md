# Contributing

Use Python 3.12 and Node.js 22. The full measurement requires a dedicated Linux x86_64 server; unit tests and page checks do not require GPUs or model API keys.

## Run the checks

From the repository root:

```bash
python3.12 -m venv bench/.venv
bench/.venv/bin/python -m pip install -r bench/requirements.txt pytest pytest-asyncio
cd bench
.venv/bin/python -m pytest -q tests
cd ..
node ui/replay/check-panel-a.cjs
node ui/replay/check-details.cjs
node ui/replay/check-shapes.cjs
```

On Ubuntu, install `tesseract-ocr`, `tesseract-ocr-eng` and `sysstat` for the job tests. Tests that need unavailable system tools may skip. The page checks use a source-level DOM harness; they do not replace checking the page in a browser.

Build the images on Linux to validate packaging:

```bash
docker build -f bench/Dockerfile -t ratio-bench:local .
docker build -f embedder/Dockerfile -t ratio-embedder:local embedder
```

CI runs the test suite, page checks and image builds. A successful build does not validate a full server measurement.

## Changes and bug reports

Describe the behavior being changed and the checks you ran. Changes to workload composition, core allocation or model timing can change the measured ratio; document those changes and compare them with the reference data.

For a failed measurement, include the last status, relevant log excerpts and the server's socket, core and memory configuration. Remove credentials and identifying server details. Keep generated run data, local environments and `.env` files out of commits.

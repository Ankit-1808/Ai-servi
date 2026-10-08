# Local Tesseract.js assets

Copy the approved local Tesseract.js browser distribution into this directory. The application expects these default filenames:

- `tesseract.min.js`
- `worker.min.js`
- `tesseract-core.wasm.js`
- `eng.traineddata`

If the approved distribution uses different names or a different language-data layout, update `js/config.js`. The application does not download OCR files from a CDN.

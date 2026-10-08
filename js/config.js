/** Configuration for the Phase 1–3 prototype. All asset paths are local. */
export const CONFIG = Object.freeze({
  workerPath: "./vendor/tesseract/worker.min.js",
  corePath: "./vendor/tesseract/tesseract-core.wasm.js",
  langPath: "./vendor/tesseract/",
  language: "eng",
  capture: { frameRate: { ideal: 1, max: 2 }, audio: false },
  preprocessing: "none",
  minimumOcrConfidence: 0
});

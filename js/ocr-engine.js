import { CONFIG } from "./config.js";

/** Loads the locally hosted Tesseract global once and keeps one worker alive. */
export class OcrEngine {
  constructor() { this.worker = null; this.status = "NOT INITIALIZED"; }

  async initialize() {
    if (this.worker) return;
    await loadScript("./vendor/tesseract/tesseract.min.js");
    if (!window.Tesseract?.createWorker) throw new Error("Local Tesseract.js loaded, but createWorker() is unavailable.");
    this.status = "INITIALIZING";
    this.worker = await window.Tesseract.createWorker(CONFIG.language, 1, { workerPath: CONFIG.workerPath, corePath: CONFIG.corePath, langPath: CONFIG.langPath, logger: () => {} });
    this.status = "READY";
  }

  async recognizeFrame(canvas) {
    if (!this.worker) throw new Error("OCR worker is not initialized.");
    const result = await this.worker.recognize(canvas);
    return normalizeResult(result);
  }

  async terminate() { if (this.worker) await this.worker.terminate(); this.worker = null; this.status = "STOPPED"; }
}

function loadScript(src) { return new Promise((resolve, reject) => { if (window.Tesseract) return resolve(); const script = document.createElement("script"); script.src = src; script.onload = resolve; script.onerror = () => reject(new Error(`Could not load local OCR library: ${src}`)); document.head.append(script); }); }
function normalizeResult(result) {
  const words = result?.data?.words ?? [];
  return words.map(word => { const box = word.bbox; return { text: word.text.trim(), confidence: Number(word.confidence ?? 0), box: { x1: box.x0, y1: box.y0, x2: box.x1, y2: box.y1 }, centerX: (box.x0 + box.x1) / 2, centerY: (box.y0 + box.y1) / 2 }; }).filter(word => word.text);
}

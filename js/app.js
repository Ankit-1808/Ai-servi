import { CONFIG } from "./config.js";
import { ScreenCapture } from "./capture.js";
import { preprocessImage } from "./preprocessing.js";
import { OcrEngine } from "./ocr-engine.js";
import { Ui } from "./ui.js";

const ui = new Ui();
const capture = new ScreenCapture(document.querySelector("#capture-preview"), document.querySelector("#frame-canvas"));
const ocr = new OcrEngine();
const startButton = document.querySelector("#start-capture");
const stopButton = document.querySelector("#stop-capture");
const runButton = document.querySelector("#run-ocr");

capture.onEnded = () => { capture.stop(); ui.setCapture("CAPTURE STOPPED"); startButton.disabled = false; stopButton.disabled = true; runButton.disabled = true; ui.message("Screen sharing ended. Start capture again to continue.", true); };

startButton.addEventListener("click", async () => {
  startButton.disabled = true; ui.message("Select the NICE CXone dashboard window in the browser dialog…");
  try { await capture.start(); ui.setCapture("CONNECTED"); stopButton.disabled = false; runButton.disabled = false; ui.message("Capture connected. Click Capture Frame & Run OCR when the dashboard is visible."); }
  catch (error) { ui.setCapture("DISCONNECTED"); startButton.disabled = false; ui.message(error.message, true); console.error(error); }
});
stopButton.addEventListener("click", () => { capture.stop(); ui.setCapture("DISCONNECTED"); startButton.disabled = false; stopButton.disabled = true; runButton.disabled = true; ui.message("Capture stopped."); });
runButton.addEventListener("click", async () => {
  runButton.disabled = true; ui.message("Capturing frame and running OCR…"); const started = performance.now();
  try { if (ocr.status !== "READY") { ui.setOcr("INITIALIZING"); await ocr.initialize(); ui.setOcr("READY"); } const frame = capture.captureCurrentFrame(); ui.showFrame(frame); const processed = preprocessImage(frame, document.querySelector("#preprocess-mode").value); const words = await ocr.recognizeFrame(processed); ui.showResults(words, performance.now() - started); console.table(words); ui.message(`OCR completed in ${((performance.now() - started) / 1000).toFixed(2)} seconds. ${words.length} words detected.`); }
  catch (error) { ui.setOcr(ocr.status === "INITIALIZING" ? "ERROR" : ocr.status); ui.message(`${error.message} Check the local Tesseract setup instructions below.`, true); console.error(error); }
  finally { runButton.disabled = !capture.connected; }
});

window.addEventListener("beforeunload", () => { capture.stop(); void ocr.terminate(); });
ui.setOcr("NOT INITIALIZED");

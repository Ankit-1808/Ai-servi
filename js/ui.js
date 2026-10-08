export class Ui {
  constructor() { this.captureStatus = document.querySelector("#capture-status"); this.ocrStatus = document.querySelector("#ocr-status"); this.messageNode = document.querySelector("#message"); this.results = document.querySelector("#ocr-results"); }
  message(text, error = false) { this.messageNode.textContent = text; this.messageNode.classList.toggle("error", error); }
  setCapture(value) { this.captureStatus.textContent = value; }
  setOcr(value) { this.ocrStatus.textContent = value; }
  showFrame(canvas) { document.querySelector("#preview-placeholder").hidden = true; document.querySelector("#frame-size").textContent = `${canvas.width} × ${canvas.height}`; }
  showResults(words, durationMs) { document.querySelector("#last-ocr").textContent = new Date().toLocaleTimeString(); document.querySelector("#ocr-duration").textContent = `${(durationMs / 1000).toFixed(2)} seconds`; this.results.replaceChildren(...(words.length ? words.map(word => { const row = document.createElement("tr"); const values = [word.text, word.confidence.toFixed(1), Math.round(word.box.x1), Math.round(word.box.y1), Math.round(word.box.x2 - word.box.x1), Math.round(word.box.y2 - word.box.y1)]; values.forEach(value => { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); }); return row; }) : [this.emptyRow()])); }
  emptyRow() { const row = document.createElement("tr"); const cell = document.createElement("td"); cell.colSpan = 6; cell.className = "empty"; cell.textContent = "No words detected."; row.append(cell); return row; }
}

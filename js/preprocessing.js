/** Returns a copied canvas so the visible preview remains unmodified. */
export function preprocessImage(source, mode = "none") {
  const output = document.createElement("canvas"); output.width = source.width; output.height = source.height;
  const context = output.getContext("2d", { willReadFrequently: true }); context.drawImage(source, 0, 0);
  if (mode === "none") return output;
  const image = context.getImageData(0, 0, output.width, output.height); const data = image.data;
  for (let i = 0; i < data.length; i += 4) {
    const gray = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    let value = gray;
    if (mode === "high-contrast") value = Math.max(0, Math.min(255, (gray - 128) * 2 + 128));
    if (mode === "binary") value = gray > 155 ? 255 : 0;
    data[i] = data[i + 1] = data[i + 2] = value;
  }
  context.putImageData(image, 0, 0); return output;
}

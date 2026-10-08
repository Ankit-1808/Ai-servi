/** Owns the display-media stream and frame extraction canvas. */
export class ScreenCapture {
  constructor(video, canvas) { this.video = video; this.canvas = canvas; this.stream = null; this.onEnded = null; }

  async start() {
    if (!navigator.mediaDevices?.getDisplayMedia) throw new Error("This browser does not support getDisplayMedia().");
    this.stream = await navigator.mediaDevices.getDisplayMedia({ video: CONFIG_CAPTURE, audio: false });
    const track = this.stream.getVideoTracks()[0];
    if (!track) throw new Error("No video track was returned by screen capture.");
    track.addEventListener("ended", () => this.onEnded?.(), { once: true });
    this.video.srcObject = this.stream;
    await this.video.play();
  }

  captureCurrentFrame() {
    if (!this.stream || this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) throw new Error("Capture preview is not ready.");
    this.canvas.width = this.video.videoWidth; this.canvas.height = this.video.videoHeight;
    this.canvas.getContext("2d", { willReadFrequently: true }).drawImage(this.video, 0, 0);
    return this.canvas;
  }

  stop() { this.stream?.getTracks().forEach(track => track.stop()); this.stream = null; this.video.pause(); this.video.srcObject = null; }
  get connected() { return Boolean(this.stream?.active); }
}

// Kept here to make the capture module independently usable without a bundler.
const CONFIG_CAPTURE = { frameRate: { ideal: 1, max: 2 } };

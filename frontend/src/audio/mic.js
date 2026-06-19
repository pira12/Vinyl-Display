// Microphone capture engine. Pulls raw mono PCM from an AudioWorklet, keeps a
// rolling buffer for recognition clips, and can also accumulate continuously
// for enrollment. Mic processing (echo cancel / noise suppress / AGC) is off so
// music isn't mangled before fingerprinting.

export class MicEngine {
  constructor() {
    this.ring = [];        // recent chunks (capped) for recent()
    this.ringLen = 0;
    this.accum = null;     // when non-null, accumulate everything for enrollment
    this.rate = 48000;
    this.maxRingSec = 30; // enough recent audio for an AcoustID identify clip
  }

  get active() {
    return !!this.ctx;
  }

  async start() {
    if (this.ctx) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
      },
    });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx();
    await this.ctx.resume();
    this.rate = this.ctx.sampleRate;
    await this.ctx.audioWorklet.addModule("/worklet/pcm-processor.js");
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-processor");
    this.node.port.onmessage = (e) => this._onFrame(e.data);
    this.source.connect(this.node);
    // Connect to destination so the worklet is pulled; it writes no output.
    this.node.connect(this.ctx.destination);

    // iOS suspends an AudioContext that produces no output, which silently
    // kills mic capture. A muted oscillator keeps it running.
    this.keepAlive = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    g.gain.value = 0;
    this.keepAlive.connect(g);
    g.connect(this.ctx.destination);
    this.keepAlive.start();
  }

  // Re-arm the context if the OS suspended it (e.g. after backgrounding).
  async resume() {
    if (this.ctx && this.ctx.state === "suspended") {
      try {
        await this.ctx.resume();
      } catch {
        /* ignore */
      }
    }
  }

  _onFrame(frame) {
    this.ring.push(frame);
    this.ringLen += frame.length;
    const max = this.rate * this.maxRingSec;
    while (this.ring.length > 1 && this.ringLen - this.ring[0].length >= max) {
      this.ringLen -= this.ring.shift().length;
    }
    if (this.accum) this.accum.push(frame);
  }

  // Most recent `seconds` of audio at the capture rate.
  recent(seconds) {
    const want = Math.floor(this.rate * seconds);
    const all = this._concat(this.ring);
    return all.length <= want ? all : all.subarray(all.length - want);
  }

  startAccum() {
    this.accum = [];
  }

  // Return everything accumulated since the last drain, then reset.
  drain() {
    const out = this._concat(this.accum || []);
    if (this.accum) this.accum = [];
    return out;
  }

  stopAccum() {
    this.accum = null;
  }

  _concat(chunks) {
    let len = 0;
    for (const c of chunks) len += c.length;
    const out = new Float32Array(len);
    let o = 0;
    for (const c of chunks) {
      out.set(c, o);
      o += c.length;
    }
    return out;
  }

  stop() {
    try {
      if (this.keepAlive) this.keepAlive.stop();
      if (this.node) this.node.disconnect();
      if (this.source) this.source.disconnect();
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      if (this.ctx) this.ctx.close();
    } catch {
      /* ignore teardown errors */
    }
    this.ctx = null;
    this.node = null;
    this.source = null;
    this.stream = null;
    this.keepAlive = null;
    this.ring = [];
    this.ringLen = 0;
    this.accum = null;
  }
}

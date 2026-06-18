// AudioWorklet that forwards raw mono PCM frames to the main thread.
// It writes no output (stays silent even when connected to the destination),
// so there's no mic-to-speaker feedback.
class PcmProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    const channel = input && input[0];
    if (channel && channel.length) {
      // The underlying buffer is reused by the engine; copy before posting.
      this.port.postMessage(new Float32Array(channel));
    }
    return true;
  }
}

registerProcessor("pcm-processor", PcmProcessor);

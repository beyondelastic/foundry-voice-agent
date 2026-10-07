// Turns browser audio into PCM16 chunks of 100 ms (2400 frames at 24 kHz).
//   input 0: the microphone
//   input 1: everything the speakers play (Vita's voice + music)
// In stereo mode each frame is [mic, speakers], so the service can subtract
// the speaker sound from the mic (live-reference echo cancellation).
const pcm16 = (s) => (s < 0 ? Math.max(-1, s) * 0x8000 : Math.min(1, s) * 0x7fff);

class MicCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.channels = options.processorOptions.channels;
    this.buffer = new Int16Array(2400 * this.channels);
    this.length = 0;
  }

  process(inputs) {
    const mic = inputs[0][0];
    const speakers = inputs[1][0];  // undefined while nothing is playing
    if (!mic) return true;
    for (let i = 0; i < mic.length; i++) {
      this.buffer[this.length++] = pcm16(mic[i]);
      if (this.channels === 2) this.buffer[this.length++] = speakers ? pcm16(speakers[i]) : 0;
      if (this.length === this.buffer.length) {
        this.port.postMessage(this.buffer.buffer.slice(0));
        this.length = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic-capture", MicCapture);

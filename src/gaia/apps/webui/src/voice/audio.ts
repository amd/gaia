// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Microphone capture and speech playback for voice, on one AudioContext.
 *
 * Capture: getUserMedia → AudioWorklet that low-passes and resamples to 16 kHz
 * mono PCM16, posted in 100 ms frames. Playback: PCM16 24 kHz frames into a
 * worklet ring buffer, resampled to the context rate, with a 120 ms jitter
 * buffer and an instant local flush for barge-in. An AnalyserNode on each side
 * gives the level the animations follow.
 */

import type { VoiceErrorCode } from './protocol';

export const MIC_RATE = 16_000;
export const SPEECH_RATE = 24_000;
const FRAME_SAMPLES = MIC_RATE / 10;
const JITTER_SAMPLES = Math.round(SPEECH_RATE * 0.12);

const MIC_WORKLET = `
class GaiaMicFrames extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / ${MIC_RATE};
    this.t = 0; this.prev = 0; this.lp = 0;
    // One-pole low-pass near 7 kHz, so decimation doesn't fold hiss into speech.
    this.alpha = 1 - Math.exp(-2 * Math.PI * 7000 / sampleRate);
    this.frame = new Int16Array(${FRAME_SAMPLES}); this.n = 0;
    this.on = true;
    this.port.onmessage = (e) => { this.on = e.data === 'resume' ? true : e.data === 'pause' ? false : this.on; if (!this.on) this.n = 0; };
  }
  process(inputs) {
    const input = inputs[0];
    const ch = input && input[0];
    if (!ch || !this.on) return true;
    const x = new Float32Array(ch.length);
    for (let i = 0; i < ch.length; i++) { this.lp += this.alpha * (ch[i] - this.lp); x[i] = this.lp; }
    const at = (k) => (k < 0 ? this.prev : x[k]);
    while (this.t < x.length - 1) {
      const i = Math.floor(this.t), f = this.t - i;
      const v = at(i) + (at(i + 1) - at(i)) * f;
      const s = Math.max(-1, Math.min(1, v));
      this.frame[this.n++] = s < 0 ? s * 32768 : s * 32767;
      if (this.n === this.frame.length) {
        const out = this.frame; this.frame = new Int16Array(${FRAME_SAMPLES}); this.n = 0;
        this.port.postMessage(out.buffer, [out.buffer]);
      }
      this.t += this.step;
    }
    this.t -= x.length;
    this.prev = x[x.length - 1];
    return true;
  }
}
registerProcessor('gaia-mic-frames', GaiaMicFrames);
`;

const SPEECH_WORKLET = `
class GaiaSpeech extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = ${SPEECH_RATE} / sampleRate;
    this.chunks = []; this.pos = 0; this.buffered = 0;
    this.playing = false; this.ending = false; this.played = 0; this.lastReport = 0;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === 'push') { this.chunks.push(m.samples); this.buffered += m.samples.length; this.ending = false; }
      else if (m.type === 'end') { this.ending = true; }
      else if (m.type === 'flush') { this.chunks = []; this.pos = 0; this.buffered = 0; this.playing = false; this.ending = false; this.port.postMessage({ type: 'idle', played: this.played }); }
    };
  }
  sample(k) {
    let i = 0;
    while (i < this.chunks.length && k >= this.chunks[i].length) { k -= this.chunks[i].length; i++; }
    return i < this.chunks.length ? this.chunks[i][k] : 0;
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!this.playing && (this.buffered >= ${JITTER_SAMPLES} || (this.ending && this.buffered > 0))) this.playing = true;
    if (!this.playing) { out.fill(0); return true; }
    for (let j = 0; j < out.length; j++) {
      if (this.buffered - this.pos < 2) { out.fill(0, j); break; }
      const i = Math.floor(this.pos), f = this.pos - i;
      const a = this.sample(i), b = this.sample(i + 1);
      out[j] = a + (b - a) * f;
      this.pos += this.step;
    }
    const whole = Math.floor(this.pos);
    let drop = whole;
    while (this.chunks.length && drop >= this.chunks[0].length) { drop -= this.chunks[0].length; this.buffered -= this.chunks[0].length; this.played += this.chunks[0].length; this.chunks.shift(); }
    this.pos -= whole - drop;
    if (this.buffered - this.pos < 2) {
      this.played += Math.max(0, this.buffered);
      this.chunks = []; this.pos = 0; this.buffered = 0; this.playing = false;
      this.port.postMessage({ type: 'idle', played: this.played });
    } else if (currentTime - this.lastReport > 0.05) {
      this.lastReport = currentTime;
      this.port.postMessage({ type: 'progress', played: this.played + Math.floor(this.pos) });
    }
    return true;
  }
}
registerProcessor('gaia-speech', GaiaSpeech);
`;

const moduleUrls = new WeakMap<BaseAudioContext, Promise<void>>();

function loadWorklets(ctx: AudioContext): Promise<void> {
    let ready = moduleUrls.get(ctx);
    if (!ready) {
        const url = (src: string) => URL.createObjectURL(new Blob([src], { type: 'application/javascript' }));
        ready = Promise.all([ctx.audioWorklet.addModule(url(MIC_WORKLET)), ctx.audioWorklet.addModule(url(SPEECH_WORKLET))]).then(() => undefined);
        moduleUrls.set(ctx, ready);
    }
    return ready;
}

/** RMS of the analyser's current window, scaled to roughly 0–1 for speech. */
function analyserLevel(analyser: AnalyserNode, buf: Float32Array<ArrayBuffer>): number {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    return Math.min(1, Math.sqrt(sum / buf.length) * 5);
}

/** The stable code for a getUserMedia failure. */
export function micErrorCode(err: unknown): VoiceErrorCode {
    const name = err instanceof DOMException || err instanceof Error ? err.name : '';
    if (name === 'NotAllowedError' || name === 'SecurityError') return 'mic_denied';
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'mic_missing';
    if (name === 'NotReadableError' || name === 'AbortError') return 'mic_busy';
    return 'mic_missing';
}

/** Whether this page can reach a microphone at all (a secure context with mediaDevices). */
export function micSupported(): boolean {
    return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof AudioWorkletNode !== 'undefined';
}

const MIC_KEY = 'gaia:voice:mic';

/** The microphone picked in Settings › Voice, per device. */
export function savedMicId(): string {
    try {
        return localStorage.getItem(MIC_KEY) ?? '';
    } catch {
        return ''; // storage blocked (private mode): the system default mic
    }
}

export function saveMicId(id: string): void {
    try {
        if (id) localStorage.setItem(MIC_KEY, id);
        else localStorage.removeItem(MIC_KEY);
    } catch (err) {
        throw new Error(`Couldn't remember the microphone on this device: ${err instanceof Error ? err.message : String(err)}`);
    }
}

/** Create the voice AudioContext. Call inside the tap or click, so a phone lets it play. */
export function createVoiceContext(): AudioContext {
    const nav = navigator as Navigator & { audioSession?: { type: string } };
    // iPhone: heard with the silent switch on, and through the speaker.
    if (nav.audioSession) nav.audioSession.type = 'play-and-record';
    const ctx = new AudioContext({ latencyHint: 'interactive' });
    void ctx.resume();
    return ctx;
}

export class MicCapture {
    private stream: MediaStream | null = null;
    private source: MediaStreamAudioSourceNode | null = null;
    private node: AudioWorkletNode | null = null;
    private sink: GainNode | null = null;
    private analyser: AnalyserNode;
    private buf: Float32Array<ArrayBuffer>;
    private paused = false;

    constructor(private ctx: AudioContext, private onFrame: (frame: ArrayBuffer) => void) {
        this.analyser = ctx.createAnalyser();
        this.analyser.fftSize = 1024;
        this.buf = new Float32Array(this.analyser.fftSize);
    }

    /** Ask for the mic and start framing. Rejects with the browser's error; see micErrorCode. */
    async start(deviceId = savedMicId()): Promise<void> {
        const audio: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
        if (deviceId) audio.deviceId = { exact: deviceId };
        const [stream] = await Promise.all([navigator.mediaDevices.getUserMedia({ audio }), loadWorklets(this.ctx)]);
        this.stream = stream;
        this.source = this.ctx.createMediaStreamSource(stream);
        this.node = new AudioWorkletNode(this.ctx, 'gaia-mic-frames', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
        this.node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => this.onFrame(e.data);
        // Pulled through a silent gain so the worklet runs without being heard.
        this.sink = this.ctx.createGain();
        this.sink.gain.value = 0;
        this.source.connect(this.analyser);
        this.source.connect(this.node).connect(this.sink).connect(this.ctx.destination);
        if (this.paused) this.pause();
    }

    /** Stop sending frames and mute the track; the mic light stays on until stop(). */
    pause(): void {
        this.paused = true;
        this.node?.port.postMessage('pause');
        this.stream?.getAudioTracks().forEach((t) => { t.enabled = false; });
    }

    resume(): void {
        this.paused = false;
        this.stream?.getAudioTracks().forEach((t) => { t.enabled = true; });
        this.node?.port.postMessage('resume');
    }

    level(): number {
        if (!this.source || this.paused) return 0;
        return analyserLevel(this.analyser, this.buf);
    }

    stop(): void {
        this.node?.port.postMessage('pause');
        this.source?.disconnect();
        this.node?.disconnect();
        this.sink?.disconnect();
        this.stream?.getTracks().forEach((t) => t.stop());
        this.stream = null;
        this.source = null;
        this.node = null;
    }
}

export class SpeechPlayer {
    private node: AudioWorkletNode | null = null;
    private analyser: AnalyserNode;
    private gain: GainNode;
    private buf: Float32Array<ArrayBuffer>;
    private ready: Promise<void>;
    /** Samples pushed so far, at 24 kHz; the timeline caption highlights read against. */
    pushed = 0;
    /** Samples heard so far. */
    played = 0;
    private busy = false;
    onIdle: () => void = () => {};

    constructor(private ctx: AudioContext) {
        this.analyser = ctx.createAnalyser();
        this.analyser.fftSize = 1024;
        this.buf = new Float32Array(this.analyser.fftSize);
        this.gain = ctx.createGain();
        this.gain.connect(ctx.destination);
        this.ready = loadWorklets(ctx).then(() => {
            this.node = new AudioWorkletNode(ctx, 'gaia-speech', { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1] });
            this.node.port.onmessage = (e: MessageEvent<{ type: string; played: number }>) => {
                this.played = e.data.played;
                if (e.data.type === 'idle' && this.busy) {
                    this.busy = false;
                    this.played = this.pushed;
                    this.onIdle();
                }
            };
            this.node.connect(this.analyser).connect(this.gain);
        });
    }

    /** Lower the voice while a sound might be the user starting to talk. */
    duck(on: boolean): void {
        this.gain.gain.setTargetAtTime(on ? 0.25 : 1, this.ctx.currentTime, 0.03);
    }

    /** Whether speech is queued or playing. */
    get playing(): boolean { return this.busy; }

    /** Queue one PCM16 24 kHz frame. */
    push(pcm: ArrayBuffer): void {
        const ints = new Int16Array(pcm);
        const samples = new Float32Array(ints.length);
        for (let i = 0; i < ints.length; i++) samples[i] = ints[i] / 32768;
        this.pushed += samples.length;
        this.busy = true;
        void this.ready.then(() => this.node?.port.postMessage({ type: 'push', samples }, [samples.buffer]));
    }

    /** No more speech is coming for now: play out what is buffered, even under the jitter target. */
    end(): void {
        void this.ready.then(() => this.node?.port.postMessage({ type: 'end' }));
    }

    /** Drop everything queued, at once. */
    flush(): void {
        this.pushed = this.played;
        this.busy = false;
        this.duck(false);
        this.node?.port.postMessage({ type: 'flush' });
    }

    level(): number {
        if (!this.busy) return 0;
        return analyserLevel(this.analyser, this.buf);
    }

    close(): void {
        this.flush();
        this.node?.disconnect();
        this.node = null;
    }
}

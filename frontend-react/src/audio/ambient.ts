/**
 * Generative ambient music, no audio files: a warm drone in D, slow plucked
 * notes from the D-major pentatonic through a felted delay, and a bed of
 * fire-crackle noise. Everything hangs off one master gain so mute is a ramp,
 * not a pop. start() must be called from a user gesture (autoplay policy).
 */

const DRONE_FREQS = [73.42, 110.0, 146.83]; // D2, A2, D3
const PENTATONIC = [293.66, 329.63, 369.99, 440.0, 493.88, 587.33]; // D4 E4 F#4 A4 B4 D5

class AmbientMusic {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private timers: number[] = [];

  start(): void {
    if (this.ctx) {
      void this.ctx.resume();
      this.master?.gain.cancelScheduledValues(this.ctx.currentTime);
      this.master?.gain.linearRampToValueAtTime(0.16, this.ctx.currentTime + 2);
      return;
    }
    const ctx = new AudioContext();
    this.ctx = ctx;
    const master = ctx.createGain();
    master.gain.setValueAtTime(0.0001, ctx.currentTime);
    master.gain.linearRampToValueAtTime(0.16, ctx.currentTime + 3);
    master.connect(ctx.destination);
    this.master = master;

    this.buildDrone(ctx, master);
    this.buildCrackle(ctx, master);
    this.schedulePluck(ctx, master);
  }

  stop(): void {
    if (!this.ctx || !this.master) return;
    this.master.gain.cancelScheduledValues(this.ctx.currentTime);
    this.master.gain.linearRampToValueAtTime(0.0001, this.ctx.currentTime + 0.8);
    const ctx = this.ctx;
    window.setTimeout(() => void ctx.suspend(), 900);
  }

  private buildDrone(ctx: AudioContext, out: AudioNode): void {
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 340;
    filter.connect(out);

    const droneGain = ctx.createGain();
    droneGain.gain.value = 0.5;
    droneGain.connect(filter);

    // A slow breath: the drone swells and recedes over ~20 seconds.
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 0.05;
    const lfoDepth = ctx.createGain();
    lfoDepth.gain.value = 0.12;
    lfo.connect(lfoDepth);
    lfoDepth.connect(droneGain.gain);
    lfo.start();

    for (const freq of DRONE_FREQS) {
      for (const detune of [-4, 4]) {
        const osc = ctx.createOscillator();
        osc.type = "triangle";
        osc.frequency.value = freq;
        osc.detune.value = detune;
        const g = ctx.createGain();
        g.gain.value = 0.09;
        osc.connect(g);
        g.connect(droneGain);
        osc.start();
      }
    }
  }

  private buildCrackle(ctx: AudioContext, out: AudioNode): void {
    const seconds = 2;
    const buffer = ctx.createBuffer(1, ctx.sampleRate * seconds, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;

    const band = ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.value = 1600;
    band.Q.value = 0.7;

    const crackleGain = ctx.createGain();
    crackleGain.gain.value = 0;

    source.connect(band);
    band.connect(crackleGain);
    crackleGain.connect(out);
    source.start();

    // Random little pops: brief gain spikes at irregular intervals.
    const pop = () => {
      if (!this.ctx) return;
      const t = ctx.currentTime;
      const strength = 0.008 + Math.random() * 0.03;
      crackleGain.gain.cancelScheduledValues(t);
      crackleGain.gain.setValueAtTime(crackleGain.gain.value, t);
      crackleGain.gain.linearRampToValueAtTime(strength, t + 0.01);
      crackleGain.gain.exponentialRampToValueAtTime(0.0008, t + 0.06 + Math.random() * 0.12);
      this.timers.push(window.setTimeout(pop, 70 + Math.random() * 350));
    };
    pop();
  }

  private schedulePluck(ctx: AudioContext, out: AudioNode): void {
    // A soft delay line so each pluck echoes once or twice, far away.
    const delay = ctx.createDelay(1.0);
    delay.delayTime.value = 0.42;
    const feedback = ctx.createGain();
    feedback.gain.value = 0.32;
    const wet = ctx.createGain();
    wet.gain.value = 0.5;
    delay.connect(feedback);
    feedback.connect(delay);
    delay.connect(wet);
    wet.connect(out);

    const pluck = () => {
      if (!this.ctx) return;
      const t = ctx.currentTime;
      const freq = PENTATONIC[Math.floor(Math.random() * PENTATONIC.length)];
      const osc = ctx.createOscillator();
      osc.type = "triangle";
      osc.frequency.value = Math.random() < 0.25 ? freq / 2 : freq;
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.linearRampToValueAtTime(0.07, t + 0.03);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 2.8);
      osc.connect(g);
      g.connect(out);
      g.connect(delay);
      osc.start(t);
      osc.stop(t + 3);
      this.timers.push(window.setTimeout(pluck, 2400 + Math.random() * 4800));
    };
    this.timers.push(window.setTimeout(pluck, 1500));
  }
}

export const ambient = new AmbientMusic();

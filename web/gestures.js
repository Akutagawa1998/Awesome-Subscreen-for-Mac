'use strict';
// A wheel stream includes the finger movement AND its momentum tail. Latch the
// whole stream, resetting only after silence, rather than after a fixed cooldown.
class PageSwipe {
  constructor({idleMs = 320, threshold = 72} = {}) {
    this.idleMs = idleMs; this.threshold = threshold; this.reset();
  }
  reset() { this.last = -Infinity; this.axis = null; this.x = 0; this.y = 0; this.fired = false; }
  wheel(dx, dy, now, mode = 0) {
    if (now - this.last > this.idleMs) this.reset();
    this.last = now; // Even tiny/vertical momentum packets extend this gesture.
    const scale = mode === 1 ? 16 : mode === 2 ? 720 : 1;
    this.x += dx * scale; this.y += dy * scale;
    if (!this.axis && Math.max(Math.abs(this.x), Math.abs(this.y)) >= 12) {
      if (Math.abs(this.x) > Math.abs(this.y) * 1.5) this.axis = 'x';
      else if (Math.abs(this.y) > Math.abs(this.x) * 1.1) this.axis = 'y';
    }
    const consume = this.axis === 'x';
    let step = 0;
    if (consume && !this.fired && Math.abs(this.x) >= this.threshold) {
      this.fired = true; step = this.x > 0 ? 1 : -1;
    }
    return {consume, step};
  }
}
function quotaTone(percent) {
  return !Number.isFinite(percent) ? 'unknown' : percent >= 95 ? 'red' : percent >= 85 ? 'orange' : percent >= 70 ? 'yellow' : 'green';
}
if (typeof module !== 'undefined') module.exports = {PageSwipe, quotaTone};
if (typeof window !== 'undefined') window.consoleUI = {PageSwipe, quotaTone};

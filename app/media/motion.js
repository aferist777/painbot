/* Motion runtime.
 *
 * One paused GSAP timeline per frame, seeked by the capture loop. Everything is
 * deterministic: the same seed gives the same reel, so a re-render reproduces it
 * and a bad roll can be re-rolled on purpose.
 *
 * Three layers of density, because one loud layer reads as twitching:
 *   bg     — never stops, never reads as an event
 *   micro  — a pulse on stressed words, felt rather than seen
 *   beats  — a real change every couple of seconds
 */
(function () {
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* A shuffled deck, not a die: uniform random repeats itself three times in a
     row often enough to read as broken. */
  function Bag(items, rand) {
    this.items = items.slice();
    this.rand = rand;
    this.deck = [];
  }
  Bag.prototype.draw = function () {
    if (!this.deck.length) {
      this.deck = this.items.slice();
      for (let i = this.deck.length - 1; i > 0; i--) {
        const j = Math.floor(this.rand() * (i + 1));
        [this.deck[i], this.deck[j]] = [this.deck[j], this.deck[i]];
      }
    }
    return this.deck.pop();
  };

  const ENTER = {
    up: { from: { opacity: 0, y: 46 } },
    down: { from: { opacity: 0, y: -38 } },
    left: { from: { opacity: 0, x: 60 } },
    right: { from: { opacity: 0, x: -60 } },
    pop: { from: { opacity: 0, scale: 0.82 } },
    tilt: { from: { opacity: 0, y: 30, rotate: -3 } },
  };

  const MICRO = {
    pulse: { scale: 1.028, duration: 0.11, yoyo: true, repeat: 1 },
    lift: { y: -7, duration: 0.12, yoyo: true, repeat: 1 },
    glow: { filter: "brightness(1.22)", duration: 0.13, yoyo: true, repeat: 1 },
    nudge: { x: 5, duration: 0.1, yoyo: true, repeat: 1 },
  };

  const MOTION = {
    tl: null,
    rand: null,
    words: [],
    duration: 6,

    init: function (options) {
      const opts = options || {};
      this.rand = mulberry32(opts.seed || 1);
      this.words = opts.words || [];
      this.duration = opts.duration || 6;
      this.enterBag = new Bag(Object.keys(ENTER), this.rand);
      this.microBag = new Bag(Object.keys(MICRO), this.rand);
      this.tl = gsap.timeline({ paused: true });
      return this.tl;
    },

    jitter: function (span) {
      return (this.rand() - 0.5) * (span === undefined ? 0.09 : span);
    },

    /* Entrance with a variant drawn from the deck. */
    enter: function (target, at, opts) {
      const o = opts || {};
      const spec = ENTER[o.anim || this.enterBag.draw()];
      this.tl.from(
        target,
        Object.assign({}, spec.from, {
          duration: o.dur || 0.55,
          ease: o.ease || "power3.out",
          stagger: o.stagger || 0,
        }),
        Math.max(at + (o.jitter === false ? 0 : this.jitter()), 0)
      );
      return this;
    },

    /* The micro layer: something moves on every Nth word of the beat. */
    micro: function (target, opts) {
      const o = opts || {};
      const every = o.every || 2;
      const nodes = typeof target === "string"
        ? Array.from(document.querySelectorAll(target)) : [target];
      if (!nodes.length) return this;
      this.words.forEach((word, index) => {
        if (index % every !== 0) return;
        const at = word.at;
        if (at < 0.25 || at > this.duration - 0.2) return;
        const node = nodes[index % nodes.length];
        const spec = MICRO[o.anim || this.microBag.draw()];
        this.tl.to(node, Object.assign({ ease: "power2.out" }, spec), at);
      });
      return this;
    },

    /* A strong change, always landed on a word so it follows the voice. */
    beat: function (target, at, opts) {
      const o = opts || {};
      this.tl.fromTo(
        target,
        { opacity: 0, scale: 0.86, y: 14 },
        {
          opacity: 1, scale: 1, y: 0,
          duration: o.dur || 0.38, ease: "back.out(2)",
        },
        Math.max(at, 0)
      );
      return this;
    },

    /* Never stops, never reads as an event. */
    background: function (target, opts) {
      const o = opts || {};
      const drift = 26 + this.rand() * 22;
      this.tl.fromTo(
        target,
        { backgroundPosition: "0px 0px", opacity: o.opacity || 1 },
        {
          backgroundPosition: drift + "px " + drift * 0.6 + "px",
          duration: this.duration, ease: "none",
        },
        0
      );
      return this;
    },

    /* Numbers that sit still read as a screenshot. Anything in a .big element
       that looks like a number counts itself up; if there is none, nothing
       happens and nothing breaks. */
    numbers: function (scope, at) {
      const nodes = document.querySelectorAll((scope || "") + " .big");
      nodes.forEach((node) => {
        const raw = node.textContent.trim();
        const match = raw.match(/^([^\d]*)([\d\s.,]+)(.*)$/);
        if (!match) return;
        const digits = match[2].replace(/\s/g, "").replace(",", ".");
        const value = parseFloat(digits);
        if (!isFinite(value) || value === 0) return;
        const decimals = (digits.split(".")[1] || "").length;
        const box = { v: 0 };
        this.tl.to(box, {
          v: value, duration: 1.1, ease: "power2.out",
          onUpdate: function () {
            node.textContent = match[1] +
              box.v.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, " ") +
              match[3];
          },
        }, at === undefined ? 0.5 : at);
      });
      return this;
    },

    /* Framing varies per beat so fourteen frames do not share one composition. */
    frame: function (target, variant) {
      const styles = {
        center: { justifyContent: "center", paddingTop: "60px", paddingBottom: "300px" },
        high: { justifyContent: "flex-start", paddingTop: "210px", paddingBottom: "220px" },
        low: { justifyContent: "flex-end", paddingTop: "180px", paddingBottom: "330px" },
      };
      const pick = variant || ["center", "high", "low"][Math.floor(this.rand() * 3)];
      Object.assign(document.getElementById("frame").style, styles[pick] || styles.center);
      return pick;
    },

    /* rough-notation animates on wall-clock, so it is drawn instantly and its
       generated paths are stroked by the timeline instead. */
    mark: function (element, at, opts) {
      const o = opts || {};
      const ann = RoughNotation.annotate(element, {
        type: o.type || "underline",
        color: o.color || "#4c8dff",
        strokeWidth: o.strokeWidth || 6,
        padding: o.padding === undefined ? 6 : o.padding,
        animate: false,
        iterations: 1,
      });
      ann.show();
      const paths = element.parentNode.querySelectorAll(".rough-annotation path");
      paths.forEach((path) => {
        const len = path.getTotalLength();
        gsap.set(path, { strokeDasharray: len, strokeDashoffset: len });
        this.tl.to(path, { strokeDashoffset: 0, duration: 0.45, ease: "power2.inOut" }, at);
      });
      return this;
    },
  };

  window.MOTION = MOTION;
  window.Bag = Bag;
})();

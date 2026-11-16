// Stoprop shared UI layer (vanilla JS, no build step). Loaded in <head> on every page.
// Theme, navigation, scroll-reveal and small progressive enhancements. Pages work without it.
(() => {
  "use strict";
  const root = document.documentElement;
  root.classList.add("js");

  // ------------------------------------------------------------------ theme (runs before first paint)
  const THEME_KEY = "stoprop.theme";
  const stored = () => { try { return localStorage.getItem(THEME_KEY); } catch { return null; } };
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const applyTheme = () => {
    const s = stored();
    root.dataset.theme = s === "light" || s === "dark" ? s : (systemDark.matches ? "dark" : "light");
  };
  applyTheme();
  systemDark.addEventListener("change", applyTheme);

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ scroll reveal
  const canObserve = "IntersectionObserver" in window && !reduced;
  const observer = (margin) => new IntersectionObserver((entries, obs) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const n = e.target;
      obs.unobserve(n);
      n.classList.add("in");
      // once the entrance has played, hand the element back to its normal styles (hover effects etc.)
      const delay = (parseFloat(getComputedStyle(n).getPropertyValue("--d")) || 0) * 90;
      setTimeout(() => n.removeAttribute("data-reveal"), 1150 + delay);
    });
  }, { rootMargin: margin, threshold: 0.01 });
  const io = canObserve ? observer("0px 0px -9% 0px") : null;
  const ioLine = canObserve ? observer("0px 0px -30% 0px") : null;   // text that lights up nearer the middle
  function reveal(nodes) {
    nodes.forEach((n) => {
      if (!n.hasAttribute("data-reveal")) n.setAttribute("data-reveal", "");
      if (!io) { n.classList.add("in"); n.removeAttribute("data-reveal"); return; }
      (n.dataset.reveal === "line" ? ioLine : io).observe(n);
    });
  }

  // ------------------------------------------------------------------ headline: word-by-word entrance
  function splitWords(h) {
    const label = h.textContent.replace(/\s+/g, " ").trim();
    let i = 0;
    const wrap = (node) => { node.classList.add("w"); node.style.setProperty("--i", i++); return node; };
    [...h.childNodes].forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const frag = document.createDocumentFragment();
        node.textContent.split(/(\s+)/).forEach((part) => {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.append(" "); return; }
          const s = document.createElement("span");
          s.textContent = part;
          frag.append(wrap(s));
        });
        node.replaceWith(frag);
      } else if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== "BR") {
        wrap(node);
      }
    });
    h.setAttribute("aria-label", label);
    h.classList.add("split");
  }

  // ------------------------------------------------------------------ hand-drawn ring (stroke is animated in style.css)
  const RING = "M22 46C14 22 62 7 110 7c50 0 82 14 80 35-2 22-48 32-96 31C48 72 10 62 13 38 15 22 46 11 80 9";
  function ring() {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "scribble");
    svg.setAttribute("viewBox", "0 0 200 80");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", RING);
    path.setAttribute("pathLength", "1");
    svg.append(path);
    return svg;
  }

  // ------------------------------------------------------------------ <select data-segmented> -> segmented control
  function segmented(select) {
    const opts = [...select.options];
    const seg = document.createElement("div");
    seg.className = "seg";
    seg.setAttribute("role", "radiogroup");
    seg.setAttribute("aria-label", select.dataset.segmented || "Options");
    const sync = () => {
      [...seg.children].forEach((b, i) => {
        const on = i === select.selectedIndex;
        b.setAttribute("aria-checked", String(on));
        b.tabIndex = on ? 0 : -1;
      });
    };
    const pick = (i, focus) => {
      if (i === select.selectedIndex) return;
      select.selectedIndex = i;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      if (focus) seg.children[i].focus();
    };
    opts.forEach((o, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.textContent = o.dataset.short || o.textContent;
      b.title = o.textContent;
      b.append(ring());   // the chosen option is circled in pencil
      b.addEventListener("click", () => pick(i, false));
      b.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (!step) return;
        e.preventDefault();
        pick((i + step + opts.length) % opts.length, true);
      });
      seg.append(b);
    });
    select.addEventListener("change", sync);
    select.hidden = true;
    select.after(seg);
    sync();
  }

  // ------------------------------------------------------------------ count-up numbers
  function countUp(node, to, { decimals = 0, duration = 1400 } = {}) {
    const fmt = (v) => v.toFixed(decimals);
    if (reduced || !("IntersectionObserver" in window)) { node.textContent = fmt(to); return; }
    node.textContent = fmt(0);
    const obs = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      obs.disconnect();
      const t0 = performance.now();
      const tick = (t) => {
        const p = Math.min(1, (t - t0) / duration);
        node.textContent = fmt(to * (1 - Math.pow(1 - p, 4)));
        if (p < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }, { threshold: 0.4 });
    obs.observe(node);
  }

  window.StopropUI = { reveal, countUp };

  document.addEventListener("DOMContentLoaded", () => {
    // nav: hairline once scrolled, mobile menu, theme toggle
    const onScroll = () => root.classList.toggle("scrolled", window.scrollY > 6);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    const nav = document.querySelector(".nav");
    const menuBtn = document.querySelector(".nav-toggle");
    if (nav && menuBtn) {
      nav.querySelectorAll(".links a").forEach((a, i) => a.style.setProperty("--i", i));
      const setOpen = (open) => {
        nav.classList.toggle("open", open);
        menuBtn.setAttribute("aria-expanded", String(open));
      };
      menuBtn.addEventListener("click", () => setOpen(!nav.classList.contains("open")));
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && nav.classList.contains("open")) { setOpen(false); menuBtn.focus(); }
      });
      document.addEventListener("click", (e) => { if (!nav.contains(e.target)) setOpen(false); });
      nav.querySelectorAll(".links a").forEach((a) => a.addEventListener("click", () => setOpen(false)));
    }
    const themeBtn = document.querySelector(".theme-toggle");
    if (themeBtn) {
      themeBtn.addEventListener("click", () => {
        const next = root.dataset.theme === "dark" ? "light" : "dark";
        try { localStorage.setItem(THEME_KEY, next); } catch { /* private mode: theme lasts for this page */ }
        root.dataset.theme = next;
      });
    }

    document.querySelectorAll("[data-split]").forEach(splitWords);
    document.querySelectorAll("select[data-segmented]").forEach(segmented);

    // entrance: explicit [data-reveal] plus every top-level block of a content page
    // (blocks that are still hidden are revealed when a page script shows them)
    const heroBits = [...document.querySelectorAll(".page-hero > *")];
    heroBits.forEach((n, i) => n.style.setProperty("--d", i));
    const blocks = [...document.querySelectorAll("main.page > *, #results > *, .principles li")]
      .filter((n) => !n.matches(".page-hero, #result, #results, .kpi-grid, .principles"));
    let above = 0;
    blocks.forEach((n) => {
      if (n.getBoundingClientRect().top < window.innerHeight && above < 4) n.style.setProperty("--d", heroBits.length + above++);
    });
    reveal([...new Set([...document.querySelectorAll("[data-reveal]"), ...heroBits, ...blocks])]);

    // glow the input card while its request is running (the page scripts disable the submit button)
    document.querySelectorAll(".glow").forEach((card) => {
      const btn = card.querySelector("button[type=submit]");
      if (!btn) return;
      new MutationObserver(() => card.classList.toggle("busy", btn.disabled))
        .observe(btn, { attributes: true, attributeFilter: ["disabled"] });
    });

    // home specimen: drag (or use the arrow keys) to compare the plain text with the marked-up one
    document.querySelectorAll(".compare").forEach((box) => {
      const range = box.querySelector(".compare-range");
      if (!range) return;
      range.addEventListener("input", () => {
        box.classList.add("touched");
        box.style.setProperty("--x", `${range.value}%`);
      });
    });

    // copy buttons on code blocks
    if (navigator.clipboard) {
      document.querySelectorAll("pre:not([data-no-copy])").forEach((pre) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "copy-btn";
        b.textContent = "Copy";
        b.addEventListener("click", async () => {
          const code = pre.querySelector("code");
          try {
            await navigator.clipboard.writeText((code || pre).textContent);
            b.textContent = "Copied";
            setTimeout(() => { b.textContent = "Copy"; }, 1600);
          } catch { /* clipboard blocked */ }
        });
        pre.append(b);
      });
    }
  });
})();

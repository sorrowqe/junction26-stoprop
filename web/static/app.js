// Stoprop web analyzer - vanilla JS, no build step.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const COLORS = {
    red:   { icon: "!", name: "Manipulation technique", desc: "A persuasion technique with high measured precision." },
    amber: { icon: "?", name: "Unsupported claim / less certain technique", desc: "No source is given in the text, or the technique is less certain. Not “false”." },
    green: { icon: "↗", name: "Claim with a source", desc: "A link, named source or dated data appears in the text. Not “true”." },
    blue:  { icon: "≈", name: "Balance marker", desc: "A fair counter-argument or an acknowledged uncertainty." },
  };
  const PRIORITY = { red: 0, amber: 1, green: 2, blue: 3 };
  const GENRES = { news: "News report", opinion: "Opinion", social: "Social media post", unknown: "Unclear" };
  const LANGS = { en: "English", et: "Estonian", ru: "Russian", other: "Other" };

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  let labels = {};          // label -> taxonomy entry
  let current = null;       // { text, result }
  let pinned = null;        // mark element with a pinned tooltip

  const labelName = (l) => (labels[l] && labels[l].name) || l.replace(/_/g, " ");

  // ---------------------------------------------- measured trade-off per sensitivity level
  let calibration = null;
  async function loadTradeoff() {
    try {
      const r = await fetch("/api/metrics");
      calibration = (await r.json()).calibration;
    } catch (e) { calibration = null; }
    showTradeoff();
  }
  function showTradeoff() {
    const el = $("sens-note");
    const lvl = calibration && calibration.levels && calibration.levels[$("sensitivity").value];
    if (!lvl) { el.textContent = ""; return; }
    const pc = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
    const a = lvl.scores.all, f = lvl.fpr_semeval_none;
    el.textContent = `Measured on our English development set: ${pc(a.precision)} of its labels match the reference labels, ` +
      `it finds ${pc(a.recall)} of the labelled techniques, and it flagged ${f.flagged} of ${f.paragraphs} ` +
      `clean news paragraphs. Small samples; details on the Metrics page.`;
  }

  // ------------------------------------------------------------- bootstrap
  async function init() {
    $("show-exp").checked = store.get("stoprop.showExp") === "1";
    const sens = store.get("stoprop.sensitivity");
    if (["strict", "balanced", "broad"].includes(sens)) $("sensitivity").value = sens;
    loadTradeoff();
    renderLegend();

    try {
      const t = await fetch("/api/techniques").then((r) => r.json());
      t.labels.forEach((x) => { labels[x.label] = x; });
    } catch { /* tooltips fall back to label ids */ }

    try {
      const samples = await fetch("/static/samples.json").then((r) => r.json());
      const box = $("samples");
      samples.forEach((s) => {
        const b = el("button", null, s.title);
        b.type = "button";
        b.addEventListener("click", () => { $("text").value = s.text; analyze(); });
        box.append(b);
      });
    } catch { /* samples are optional */ }

    $("form").addEventListener("submit", (e) => { e.preventDefault(); analyze(); });
    $("text").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); analyze(); }
    });
    $("show-exp").addEventListener("change", () => {
      store.set("stoprop.showExp", $("show-exp").checked ? "1" : "0");
      if (current) render();
    });
    $("sensitivity").addEventListener("change", () => {
      store.set("stoprop.sensitivity", $("sensitivity").value);
      showTradeoff();
      if (current && $("text").value === current.text) analyze();
    });
    $("refresh-receipts").addEventListener("click", refreshReceipts);
    wireTooltip();
  }

  // --------------------------------------------------------------- analyze
  async function analyze() {
    const text = $("text").value;
    const url = $("url").value.trim();
    const status = $("status");
    status.className = "status";
    if (!text.trim() && !url) {
      status.textContent = "Please paste some text or a link first.";
      status.className = "status error";
      $("text").focus();
      return;
    }
    $("go").disabled = true;
    status.textContent = text.trim() ? "Analyzing…" : "Fetching the page and analyzing…";
    try {
      const r = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim() ? text : null, url: url || null, sensitivity: $("sensitivity").value || null,
          lang: "auto", private: $("private").checked }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const msg = typeof body.detail === "string" ? body.detail : `Something went wrong (HTTP ${r.status}).`;
        throw new Error(msg);
      }
      const analyzed = typeof body.text === "string" ? body.text : text;
      if (typeof body.text === "string") $("text").value = body.text;  // show the fetched article text
      current = { text: analyzed, url, result: body };
      status.textContent = "";
      render();
      $("result").hidden = false;
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      status.textContent = err.message || "Could not reach the server.";
      status.className = "status error";
    } finally {
      $("go").disabled = false;
    }
  }

  // ---------------------------------------------------------------- render
  function visibleSpans() {
    const showExp = $("show-exp").checked;
    return current.result.spans
      .map((s, i) => ({ ...s, idx: i }))
      .filter((s) => showExp || !s.experimental);
  }

  function render() {
    hideTip(true);
    const res = current.result;
    const spans = visibleSpans();

    const langNote = $("lang-note");
    langNote.hidden = !res.language_note;
    langNote.textContent = res.language_note || "";
    const banner = $("banner");
    const msg = bannerText(res);
    banner.hidden = !msg;
    banner.textContent = msg || "";

    renderDoc(current.text, spans);
    renderFindings(spans, res);
    renderProfile(res);
    renderInnocent(spans, res);
    renderSource(res);
  }

  function renderSource(res) {
    const card = $("source-card");
    const receipts = res.receipts || [];
    card.hidden = !receipts.length && !res.source;
    $("receipts-status").textContent = "";
    const info = $("source-info");
    info.textContent = "";
    if (res.source) {
      const a = el("a", null, res.source.title || res.source.final_url);
      a.href = res.source.final_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      info.append(a);
      const bits = [res.source.domain, res.source.date].filter(Boolean);
      if (bits.length) info.append(el("span", "muted", ` · ${bits.join(" · ")}`));
    } else if (current.url) {
      info.append(el("span", "muted", `Receipts for ${current.url}`));
    }
    window.StopropReceipts.render($("receipts"), receipts);
    $("refresh-receipts").hidden = !receipts.length;
  }

  async function refreshReceipts() {
    const res = current && current.result;
    const target = (res && res.source && res.source.final_url) || (current && current.url);
    if (!target) return;
    const btn = $("refresh-receipts");
    btn.disabled = true;
    $("receipts-status").textContent = "Checking again…";
    try {
      const data = await window.StopropReceipts.refresh(target, res.source && res.source.title);
      res.receipts = data.receipts;
      window.StopropReceipts.render($("receipts"), data.receipts);
      $("receipts-status").textContent = "Updated. Each check can be refreshed at most once every 15 minutes.";
    } catch (err) {
      $("receipts-status").textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  }

  function bannerText(res) {
    const rules = " Showing simple word-rule hints only; they are experimental and stay hidden unless you switch on “Show experimental labels”.";
    if (res.mode === "heuristics_only") {
      if (res.mode_reason === "budget") return "Today's AI budget is used up." + rules;
      if (res.mode_reason === "llm_error") return "AI analysis is temporarily unavailable." + rules;
      return "AI analysis is not switched on on this server." + rules;
    }
    const hidden = res.spans.filter((s) => s.experimental).length;
    if (hidden && !$("show-exp").checked) {
      return "Every technique is still experimental until we have measured its precision on a labelled dataset, " +
        "so AI findings are hidden by default. Switch on “Show experimental labels” to see them.";
    }
    return "";
  }

  function renderInnocent(spans, res) {
    const card = $("innocent-card");
    const list = $("innocent");
    list.textContent = "";
    const anyFlag = spans.some((s) => s.color === "red" || s.color === "amber");
    const items = res.innocent_explanations || [];
    card.hidden = !(anyFlag && items.length);
    items.forEach((t) => list.append(el("li", null, t)));
  }

  function modeText(res) {
    if (res.mode !== "llm") return "Rules only";
    if (res.cost && res.cost.cached) return "AI, from cache (no new cost)";
    const c = res.cost || {};
    const tokens = (c.input_tokens || 0) + (c.output_tokens || 0);
    return `AI, ${c.llm_calls || 0} call${c.llm_calls === 1 ? "" : "s"}, ${tokens.toLocaleString("en")} tokens`;
  }

  // Server offsets are Unicode code points; JS strings index UTF-16 units.
  function cpToUnit(text) {
    if (!/[\uD800-\uDFFF]/.test(text)) return (i) => i;
    const map = [];
    let unit = 0;
    for (const ch of text) { map.push(unit); unit += ch.length; }
    map.push(unit);
    return (i) => map[Math.min(i, map.length - 1)];
  }

  const byPriority = (a, b) => PRIORITY[a.color] - PRIORITY[b.color] || b.confidence - a.confidence;

  function renderDoc(text, spans) {
    const doc = $("doc");
    doc.textContent = "";
    const toUnit = cpToUnit(text);
    const items = spans.map((s) => ({ ...s, a: toUnit(s.start), b: toUnit(s.end) }));
    const points = new Set([0, text.length]);
    items.forEach((s) => { points.add(s.a); points.add(s.b); });
    const pts = [...points].sort((x, y) => x - y);

    for (let k = 0; k < pts.length - 1; k++) {
      const a = pts[k], b = pts[k + 1];
      const chunk = text.slice(a, b);
      const cover = items.filter((s) => s.a <= a && s.b >= b).sort(byPriority);
      if (!cover.length) { doc.append(chunk); continue; }
      const m = el("mark", `hl c-${cover[0].color}`, chunk);
      m.dataset.spans = cover.map((s) => s.idx).join(",");
      m.tabIndex = 0;
      m.setAttribute("role", "button");
      m.setAttribute("aria-label", `${chunk}: ${cover.map((s) => labelName(s.label)).join(", ")}. Press Enter for details.`);
      doc.append(m);
      cover.filter((s) => s.b === b).forEach((s) => {
        const ic = el("span", `ico c-${s.color}`, COLORS[s.color].icon);
        ic.setAttribute("aria-hidden", "true");
        doc.append(ic);
      });
    }
  }

  function renderFindings(spans, res) {
    const list = $("findings");
    const empty = $("empty");
    list.textContent = "";
    empty.textContent = "";
    const hiddenExp = res.spans.filter((s) => s.experimental).length - spans.filter((s) => s.experimental).length;

    if (!spans.length) {
      empty.hidden = false;
      if (hiddenExp > 0) {
        empty.append(el("p", null,
          `Nothing is flagged by default. ${hiddenExp} experimental ${hiddenExp === 1 ? "hint is" : "hints are"} hidden ` +
          "because their precision has not been measured yet."));
        const b = el("button", null, "Show experimental labels");
        b.type = "button";
        b.addEventListener("click", () => { $("show-exp").checked = true; $("show-exp").dispatchEvent(new Event("change")); });
        empty.append(b);
      } else {
        empty.append(el("p", null,
          "Nothing found. That is a valid result: Stoprop would rather say nothing than guess."));
      }
      return;
    }
    empty.hidden = true;

    spans.forEach((s) => {
      const li = el("li");
      const b = el("button");
      b.type = "button";
      const head = el("span", null);
      head.append(el("span", `ico c-${s.color}`, COLORS[s.color].icon), " ", el("strong", null, labelName(s.label)), " ");
      head.append(el("span", "q", `“${s.quote}”`));
      b.append(head);
      const meta = [`confidence ${Math.round(s.confidence * 100)}%`];
      if (s.experimental) meta.push("experimental");
      if (s.reported_speech) meta.push("reported speech, not counted");
      b.append(el("span", "meta", meta.join(" · ")));
      b.addEventListener("click", (e) => {
        e.stopPropagation(); // keep the document "click outside" handler from closing the tip
        const mark = [...document.querySelectorAll("#doc mark")].find((m) => m.dataset.spans.split(",").includes(String(s.idx)));
        if (mark) { mark.scrollIntoView({ block: "center", behavior: "smooth" }); mark.focus({ preventScroll: true }); showTip(mark, true); }
      });
      li.append(b);
      list.append(li);
    });
  }

  function renderProfile(res) {
    const p = res.profile;
    $("summary").textContent = p.summary;
    const dl = $("profile");
    dl.textContent = "";
    const counts = Object.entries(p.counts || {});
    const rows = [
      ["Genre", GENRES[res.genre] || res.genre],
      ["Language", LANGS[res.lang] || res.lang],
      ["Words", p.words.toLocaleString("en")],
      ["Text flagged", `${Math.round(p.flagged_share * 100)}%`],
      ["Density", `${p.density_per_1000} per 1,000 words`],
      ["Techniques", counts.length ? counts.map(([l, n]) => `${labelName(l)} × ${n}`).join(", ") : "none by default"],
      ["Experimental", `${p.experimental.count} ${$("show-exp").checked ? "(shown)" : "(hidden)"}`],
      ["Reported speech", `${p.reported_speech} (not counted)`],
      ["Mode", modeText(res)],
    ];
    if (res.stats && res.stats.seen_before > 0) {
      rows.push(["Seen before", `${res.stats.seen_before} earlier ${res.stats.seen_before === 1 ? "analysis" : "analyses"} of this text`]);
    }
    rows.forEach(([k, v]) => { dl.append(el("dt", null, k), el("dd", null, v)); });
    const notes = $("notes");
    notes.textContent = "";
    (res.notes || []).forEach((n) => notes.append(el("li", null, n)));
  }

  function renderLegend() {
    const ul = $("legend");
    Object.entries(COLORS).forEach(([c, info]) => {
      const li = el("li");
      li.append(el("mark", `hl c-${c}`, info.name), el("span", `ico c-${c}`, info.icon));
      li.firstChild.removeAttribute("role");
      li.lastChild.setAttribute("aria-hidden", "true");
      li.append(el("span", "d", info.desc));
      ul.append(li);
    });
  }

  // --------------------------------------------------------------- tooltip
  function tipContent(mark) {
    const frag = document.createDocumentFragment();
    mark.dataset.spans.split(",").map(Number).forEach((i) => {
      const s = current.result.spans[i];
      const t = labels[s.label] || {};
      const item = el("div", "item");
      const h = el("h4");
      h.append(el("span", `ico c-${s.color}`, COLORS[s.color].icon), labelName(s.label));
      item.append(h);
      if (t.definition) item.append(el("p", "muted", t.definition));
      if (s.explanation) {
        const why = el("p");
        why.append(el("strong", null, "Why here: "), s.explanation);
        item.append(why);
      }
      item.append(el("p", "small", `Confidence: ${Math.round(s.confidence * 100)}% · ${COLORS[s.color].name}`));
      const badges = el("div", "badges");
      if (s.reported_speech) badges.append(el("span", "badge rs", "Reported speech: quoted from someone else, not counted against the source"));
      if (s.experimental) badges.append(el("span", "badge exp", "Experimental: precision not yet measured"));
      if (badges.childNodes.length) item.append(badges);
      frag.append(item);
    });
    return frag;
  }

  function showTip(mark, pin) {
    const tip = $("tip");
    document.querySelectorAll("#doc mark.active").forEach((m) => m.classList.remove("active"));
    tip.textContent = "";
    tip.append(tipContent(mark));
    tip.hidden = false;
    mark.classList.add("active");
    mark.setAttribute("aria-describedby", "tip");
    const r = mark.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const left = Math.max(16, Math.min(r.left + window.scrollX, window.scrollX + vw - tip.offsetWidth - 16));
    let top = r.bottom + window.scrollY + 8;
    if (r.bottom + tip.offsetHeight + 16 > window.innerHeight && r.top > tip.offsetHeight + 16) {
      top = r.top + window.scrollY - tip.offsetHeight - 8;
    }
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
    pinned = pin ? mark : pinned;
  }

  function hideTip(force) {
    if (pinned && !force) return;
    pinned = null;
    $("tip").hidden = true;
    document.querySelectorAll("#doc mark.active").forEach((m) => { m.classList.remove("active"); m.removeAttribute("aria-describedby"); });
  }

  function wireTooltip() {
    const doc = $("doc");
    const markOf = (e) => e.target.closest && e.target.closest("mark.hl");
    doc.addEventListener("mouseover", (e) => { const m = markOf(e); if (m && !pinned) showTip(m, false); });
    doc.addEventListener("mouseout", (e) => { if (markOf(e) && !pinned) hideTip(false); });
    doc.addEventListener("focusin", (e) => { const m = markOf(e); if (m && (!pinned || pinned === m)) showTip(m, pinned === m); });
    doc.addEventListener("focusout", (e) => { if (markOf(e) && !pinned) hideTip(false); });
    doc.addEventListener("click", (e) => {
      const m = markOf(e);
      if (!m) return;
      e.stopPropagation();
      if (pinned === m) hideTip(true); else { hideTip(true); showTip(m, true); }
    });
    doc.addEventListener("keydown", (e) => {
      const m = markOf(e);
      if (m && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); m.click(); }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("tip").hidden) { const m = pinned; hideTip(true); if (m) m.focus(); }
    });
    document.addEventListener("click", (e) => { if (!e.target.closest("#tip")) hideTip(true); });
    window.addEventListener("resize", () => hideTip(true));
  }

  init();
})();

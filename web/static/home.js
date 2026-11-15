// Home page "numbers" band: read live from GET /api/metrics, never typed into the page.
// If the benchmarks are missing or the request fails, the band simply stays hidden.
(() => {
  "use strict";
  const band = document.getElementById("numbers");
  const box = document.getElementById("stats");
  if (!band || !box) return;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  function tile({ value, decimals, suffix, name, sub }) {
    const t = el("div", "stat");
    const val = el("div", "stat-val");
    const num = el("span", null, "0");
    val.append(num);
    if (suffix) val.append(el("small", null, suffix));
    t.append(val, el("p", "stat-name", name), el("p", "stat-sub", sub));
    window.StopropUI.countUp(num, value, { decimals: decimals || 0 });
    return t;
  }

  fetch("/api/metrics").then((r) => r.json()).then((m) => {
    const tiles = [];
    const strict = m.calibration && m.calibration.levels && m.calibration.levels.strict;
    const all = strict && strict.scores && strict.scores.all;
    if (all && typeof all.precision === "number") {
      tiles.push({
        value: all.precision * 100, decimals: 1, suffix: "%", name: "Label precision",
        sub: `Share of ${all.predicted} flagged passages that match the human reference labels (English development set, Strict).`,
      });
    }
    const news = strict && strict.fpr_semeval_none;
    if (news && news.paragraphs) {
      const hard = strict.fpr_synthetic_hard_negative || { flagged: 0, paragraphs: 0 };
      const total = news.paragraphs + hard.paragraphs, flagged = news.flagged + hard.flagged;
      tiles.push({
        value: total - flagged, suffix: `/ ${total}`, name: "Clean passages left alone",
        sub: `Neutral news and fair opinion controls; ${plural(flagged, "false alarm", "false alarms")} at the default Strict setting.`,
      });
    }
    const sw = m.swarm;
    if (sw && sw.coordinated_scenarios) {
      tiles.push({
        value: sw.tp, suffix: `/ ${sw.coordinated_scenarios}`, name: "Coordinated waves caught",
        sub: `${sw.innocent_scenarios - sw.fp} of ${sw.innocent_scenarios} organic crowds (viral headlines, quotes, event hashtags) were not accused.`,
      });
    }
    const tr = m.traffic;
    if (tr && tr.bad_scenarios) {
      tiles.push({
        value: tr.tp, suffix: `/ ${tr.bad_scenarios}`, name: "Bot waves separated",
        sub: `${tr.innocent_scenarios - tr.fp} of ${tr.innocent_scenarios} real-visitor, shared-office and verified-crawler controls stayed unblocked.`,
      });
    }
    if (!tiles.length) return;
    const nodes = tiles.map(tile);
    nodes.forEach((n, i) => n.style.setProperty("--d", i));
    box.replaceChildren(...nodes);
    band.hidden = false;
    window.StopropUI.reveal(nodes);
  }).catch(() => { /* band stays hidden */ });
})();

// Clean, user-focused /metrics renderer (vanilla JS). Data: GET /api/metrics + GET /api/techniques.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 1000) / 10}%`);
  const num = (x) => (x === null || x === undefined ? "–" : String(x));
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const row = (cells) => {
    const tr = document.createElement("tr");
    cells.forEach((c, i) => {
      const td = document.createElement(i === 0 ? "th" : "td");
      if (i === 0) td.scope = "row";
      td.textContent = c;
      tr.append(td);
    });
    return tr;
  };
  const body = (id) => $(id).querySelector("tbody");
  const SOURCE_NAMES = {
    semeval: "SemEval-2020 news corpus (human-labelled)",
    synthetic: "Controlled multi-technique test set",
    semeval_none: "Clean news paragraphs (SemEval human-labelled 'none')",
    synthetic_hard_negative: "Hard negatives (emotional factual reports & fair opinions)",
  };
  const LEVEL_NAMES = { strict: "Strict (default)", balanced: "Balanced", broad: "Broad (all detections)" };
  const VERDICT_NAMES = {
    likely_coordinated: "Likely coordinated",
    worth_watching: "Worth watching",
    organic_looking: "Looks organic",
    insufficient_data: "Not enough data",
    none: "Clean (no repeated groups)",
  };

  function wilsonUpper(k, n) {
    if (!n) return null;
    const z = 1.96, p = k / n, z2 = z * z;
    const denom = 1 + z2 / n;
    const center = p + z2 / (2 * n);
    const half = z * Math.sqrt((p * (1 - p) + z2 / (4 * n)) / n);
    return Math.min(1, (center + half) / denom);
  }

  function renderKpis(calib, swarm) {
    const grid = $("kpi-grid");
    grid.replaceChildren();
    const tiles = [];
    const strict = calib && calib.levels && calib.levels.strict;
    if (strict && strict.fpr_semeval_none) {
      const f = strict.fpr_semeval_none;
      const hi = f.ci ? pct(f.ci[1]) : pct(wilsonUpper(f.flagged, f.paragraphs));
      tiles.push([
        "Clean-news false alarms",
        `${f.flagged} / ${f.paragraphs} (${pct(f.rate)})`,
        `Human-labelled clean news · 95% CI 0–${hi}`,
      ]);
      const h = strict.fpr_synthetic_hard_negative || { flagged: 0, paragraphs: 0 };
      const totFlag = f.flagged + h.flagged;
      const totN = f.paragraphs + h.paragraphs;
      const totHi = pct(wilsonUpper(totFlag, totN));
      tiles.push([
        "All clean + opinion controls",
        `${totFlag} / ${totN} (${pct(totN ? totFlag / totN : 0)})`,
        `21 clean news + ${h.paragraphs} emotional/opinion · 95% CI 0–${totHi}`,
      ]);
    }
    if (swarm) {
      tiles.push([
        "Swarm campaign F1",
        pct(swarm.f1),
        `${swarm.tp} / ${swarm.coordinated_scenarios} coordinated waves caught (P ${pct(swarm.precision)}, R ${pct(swarm.recall)})`,
      ]);
      const swHi = pct(wilsonUpper(swarm.fp, swarm.innocent_scenarios));
      tiles.push([
        "Swarm innocent false alarms",
        `${swarm.fp} / ${swarm.innocent_scenarios} (${pct(swarm.innocent_fpr)})`,
        `Headlines, quotes, syndication & events · 95% CI 0–${swHi}`,
      ]);
    }
    if (!tiles.length) return;
    grid.hidden = false;
    tiles.forEach(([title, value, sub]) => {
      const card = el("div", "card kpi-card");
      card.append(el("div", "kpi-title muted small", title), el("div", "kpi-val", value), el("div", "muted small", sub));
      grid.append(card);
    });
    const note = $("kpi-note");
    if (note) {
      note.hidden = false;
      note.textContent = "How to read X / N: N is the number of benchmark passages or multi-post batches tested. " +
        "Because N is finite (21–25 text controls, 40 Swarm batches), we also show the 95% Wilson confidence upper bound — " +
        "0 false alarms in a sample means a low error rate, never a mathematical guarantee of 0% on every future article.";
    }
  }

  function renderCalibration(c) {
    if (!c || !c.levels) return;
    $("calib-card").hidden = false;
    const frac = (o) => (o && o.paragraphs !== undefined ? `${num(o.flagged)} / ${num(o.paragraphs)} (${pct(o.rate)})` : "–");
    ["strict", "balanced", "broad"].forEach((lvl) => {
      const v = c.levels[lvl];
      if (!v) return;
      const a = v.scores.all;
      body("calib").append(row([
        LEVEL_NAMES[lvl] || lvl,
        pct(a.precision),
        pct(a.recall),
        pct(a.f1),
        frac(v.fpr_semeval_none),
        frac(v.fpr_synthetic_hard_negative),
      ]));
    });
  }

  function renderLatest(r, labelMap) {
    ["strict", "lenient"].forEach((mode) => {
      const s = r.scores[mode];
      Object.entries(s.by_source || {}).forEach(([src, v]) => {
        const m = v.micro;
        body("overall").append(row([
          SOURCE_NAMES[src] || src,
          mode === "strict" ? "Strict (quotes excluded)" : "Lenient (quotes included)",
          pct(m.precision), pct(m.recall), pct(m.f1), num(m.predicted), num(m.gold),
        ]));
      });
    });

    const per = r.scores.strict.all.per_label || {};
    Object.entries(per)
      .sort((a, b) => (b[1].gold || 0) - (a[1].gold || 0))
      .forEach(([lbl, m]) => {
        if (!m.gold && !m.predicted) return;
        const human = labelMap[lbl] || lbl.replace(/_/g, " ");
        body("per-label").append(row([human, pct(m.precision), pct(m.recall), pct(m.f1), num(m.predicted), num(m.gold)]));
      });

    Object.entries(r.clean_fpr || {}).forEach(([set, v]) => {
      body("fpr").append(row([
        SOURCE_NAMES[set] || set,
        num(v.paragraphs),
        `${num(v.flagged_techniques)} (${pct(v.fpr_techniques)})`,
        num(v.flags_per_1000_words),
      ]));
    });

    if (r.symmetry && r.symmetry.pairs) {
      const s = r.symmetry;
      const consistent = s.pairs - s.flipped;
      $("sym").textContent = `Symmetry check across ${s.pairs} mirrored pairs (identical arguments with opposing sides swapped): ` +
        `${consistent} of ${s.pairs} (${pct(consistent / s.pairs)}) received identical technique sets regardless of viewpoint.`;
    }
  }

  function renderSwarm(sw) {
    if (!sw || !Array.isArray(sw.details)) return;
    $("swarm-card").hidden = false;
    $("swarm-summary").textContent = `Evaluated across ${sw.scenarios} multi-post scenarios (${sw.coordinated_scenarios} coordinated ` +
      `campaigns and ${sw.innocent_scenarios} organic / innocent-pattern batches): precision ${pct(sw.precision)}, ` +
      `recall ${pct(sw.recall)}, F1 ${pct(sw.f1)}, and ${sw.fp}/${sw.innocent_scenarios} (${pct(sw.innocent_fpr)}) false positives on innocent sharing.`;
    sw.details.forEach((d) => {
      body("swarm-table").append(row([
        d.label,
        d.kind === "coordinated" ? "Coordinated campaign" : "Organic / innocent control",
        num(d.posts),
        VERDICT_NAMES[d.top_verdict] || d.top_verdict,
        d.passed ? "✓ Correct" : "× Missed",
      ]));
    });
  }

  Promise.all([
    fetch("/api/metrics").then((r) => r.json()),
    fetch("/api/techniques").then((r) => r.json()).catch(() => ({ labels: [] })),
  ]).then(([data, tax]) => {
    const labelMap = {};
    (tax.labels || []).forEach((t) => { labelMap[t.label] = t.name; });

    // Clean 2-row language summary: English vs. Other languages
    const enEval = (data.languages || []).some((l) => l.lang === "en" && l.evaluated);
    const lb = body("lang-table");
    if (lb) {
      lb.replaceChildren(
        row(["English", enEval ? "Benchmarked" : "Supported"]),
        row(["Other languages", "Supported (not yet benchmarked)"]),
      );
    }

    const latest = data.latest;
    const hasAny = Boolean(latest || data.swarm || data.calibration);
    if ($("no-results")) $("no-results").hidden = hasAny;
    if ($("results")) $("results").hidden = !latest;

    try { renderKpis(data.calibration, data.swarm); } catch { /* isolated */ }
    try { if (latest) renderLatest(latest, labelMap); } catch { /* isolated */ }
    try { renderCalibration(data.calibration); } catch { /* isolated */ }
    try { renderSwarm(data.swarm); } catch { /* isolated */ }
  }).catch(() => {
    if ($("no-results")) $("no-results").hidden = false;
  });
})();

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

  function wilsonBounds(k, n) {
    if (!n) return [0, 1];
    const z = 1.96, p = k / n, z2 = z * z;
    const denom = 1 + z2 / n;
    const center = p + z2 / (2 * n);
    const half = z * Math.sqrt((p * (1 - p) + z2 / (4 * n)) / n);
    return [Math.max(0, (center - half) / denom), Math.min(1, (center + half) / denom)];
  }

  function renderKpis(calib, latest, swarm) {
    const grid = $("kpi-grid");
    grid.replaceChildren();
    const tiles = [];
    const strict = calib && calib.levels && calib.levels.strict;
    if (strict && strict.scores && strict.scores.all) {
      const s = strict.scores.all;
      const broadF1 = latest && latest.scores && latest.scores.strict && latest.scores.strict.all
        ? latest.scores.strict.all.micro.f1
        : null;
      tiles.push([
        "Text detector precision",
        pct(s.precision),
        `Strict F1 ${pct(s.f1)}${broadF1 != null ? ` · Broad F1 ${pct(broadF1)}` : ""} (89 benchmark passages)`,
      ]);
    }
    if (strict && strict.fpr_semeval_none) {
      const f = strict.fpr_semeval_none;
      const h = strict.fpr_synthetic_hard_negative || { flagged: 0, paragraphs: 0 };
      const totFlag = f.flagged + h.flagged;
      const totN = f.paragraphs + h.paragraphs;
      const totClean = totN - totFlag;
      const [lo, hi] = wilsonBounds(totClean, totN);
      tiles.push([
        "Clean text pass rate",
        `${totClean} / ${totN} clean (${pct(totN ? totClean / totN : 0)})`,
        `${totFlag} false ${totFlag === 1 ? "alarm" : "alarms"} across ${f.paragraphs} news + ${h.paragraphs} opinion controls · 95% CI ${pct(lo)}–${pct(hi)}`,
      ]);
    }
    if (swarm) {
      tiles.push([
        "Swarm campaign F1",
        pct(swarm.f1),
        `${swarm.tp} / ${swarm.coordinated_scenarios} coordinated waves caught (P ${pct(swarm.precision)}, R ${pct(swarm.recall)})`,
      ]);
      const cleanBatches = swarm.innocent_scenarios - swarm.fp;
      const [swLo, swHi] = wilsonBounds(cleanBatches, swarm.innocent_scenarios);
      tiles.push([
        "Organic crowd pass rate",
        `${cleanBatches} / ${swarm.innocent_scenarios} clean (${pct(cleanBatches / swarm.innocent_scenarios)})`,
        `${swarm.fp} false alarms on viral headlines, quotes & events · 95% CI ${pct(swLo)}–${pct(swHi)}`,
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
      note.textContent = "Pass-rate cards show how many non-manipulative control passages (25 text controls) and organic " +
        "multi-post threads (20 Swarm controls) passed without false accusations, alongside their 95% Wilson confidence interval.";
    }
  }

  function renderCalibration(c) {
    if (!c || !c.levels) return;
    $("calib-card").hidden = false;
    const frac = (o) => (o && o.paragraphs !== undefined ? `${num(o.flagged)} / ${num(o.paragraphs)} flagged (${pct(o.rate)})` : "–");
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

    try { renderKpis(data.calibration, latest, data.swarm); } catch { /* isolated */ }
    try { if (latest) renderLatest(latest, labelMap); } catch { /* isolated */ }
    try { renderCalibration(data.calibration); } catch { /* isolated */ }
    try { renderSwarm(data.swarm); } catch { /* isolated */ }
  }).catch(() => {
    if ($("no-results")) $("no-results").hidden = false;
  });
})();

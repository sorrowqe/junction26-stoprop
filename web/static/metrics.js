// /metrics renderer (vanilla JS). Data: GET /api/metrics.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 1000) / 10}%`);
  const num = (x) => (x === null || x === undefined ? "–" : String(x));
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
  const nice = (s) => (s || "").replace(/_/g, " ");

  function render(data) {
    (data.languages || []).forEach((l) => body("lang-table").append(
      row([l.name, l.evaluated ? "Measured" : "Not measured"])));

    const latest = data.latest;
    $("no-results").hidden = !!latest;
    $("results").hidden = !latest;
    if (latest) renderLatest(latest);
    renderBakeoff(data.bakeoff);
    renderCalibration(data.calibration);
    renderSelf(data.self_consistency, latest);

    const review = data.ai_review;
    const gold = data.gold_report;
    if (review || gold) {
      $("gold-card").hidden = false;
      const bits = [];
      if (gold) {
        const src = Object.entries(gold.per_source || {}).map(([s, c]) => `${s}: ${c.accepted || 0} accepted, ${c.dropped || 0} dropped, ${c.excluded || 0} excluded`);
        bits.push(`Paragraphs used (English): ${src.join("; ")}.`);
        if (gold.symmetry_pairs) bits.push(`Symmetry pairs kept: ${gold.symmetry_pairs.accepted || 0} (a pair is kept only if both variants are accepted).`);
      }
      if (review) {
        bits.push(`Synthetic rows validated by ${review.reviewers.join(" and ")} (${review.method}). ` +
          `Raw agreement ${pct(review.raw_agreement)}, Cohen's κ = ${num(review.kappa)}; ` +
          `reported-speech corrections applied: ${review.reported_speech_corrections}.`);
        const rates = review.accept_rate || {};
        bits.push(`Acceptance rate: ${Object.entries(rates).map(([m, r]) => `${m} ${pct(r)}`).join(", ")}.`);
        const rubber = Object.entries(rates).filter(([, r]) => r === 1);
        if (rubber.length) {
          bits.push(`Note: ${rubber.map(([m]) => m).join(", ")} accepted every row, so κ is 0 despite the high raw ` +
            "agreement; in practice the other reviewer's rejections decided which items were removed.");
        }
        Object.entries(review.per_label || {}).forEach(([lbl, c]) => body("gold-labels").append(
          row([nice(lbl), num(c.accepted || 0), num(c.dropped || 0), num(c.excluded || 0), num(c.kappa)])));
      }
      $("gold-info").textContent = bits.join(" ");
      $("gold-labels").closest(".table-wrap").hidden = !review;
    }
  }

  function renderLatest(r) {
    $("run-info").textContent = `Model ${r.model} · ${r.split} split · ${r.language === "en" ? "English" : r.language} · ` +
      `sensitivity ${r.sensitivity} · ${r.counts.items} paragraphs · commit ${r.git_commit} · ${r.timestamp}`;
    ["strict", "lenient"].forEach((mode) => {
      const s = r.scores[mode];
      Object.entries(s.by_source).forEach(([src, v]) => {
        const m = v.micro;
        body("overall").append(row([src, mode, pct(m.precision), pct(m.recall), pct(m.f1), num(m.predicted), num(m.gold)]));
      });
    });
    const per = r.scores.strict.all.per_label;
    Object.entries(per).forEach(([lbl, m]) => body("per-label").append(
      row([nice(lbl), pct(m.precision), pct(m.recall), pct(m.f1), num(m.predicted), num(m.gold)])));
    const weak = Object.entries(per).filter(([, m]) => m.gold >= 3 || m.predicted >= 3)
      .sort((a, b) => (a[1].f1 ?? 0) - (b[1].f1 ?? 0)).slice(0, 3);
    if (!weak.length) {
      const li = document.createElement("li");
      li.textContent = "Not enough labelled examples yet to say (needs at least 3 per technique).";
      $("weak").append(li);
    }
    weak.forEach(([lbl, m]) => {
      const li = document.createElement("li");
      li.textContent = `${nice(lbl)}: F1 ${pct(m.f1)} (precision ${pct(m.precision)}, recall ${pct(m.recall)}).`;
      $("weak").append(li);
    });
    Object.entries(r.clean_fpr || {}).forEach(([name, f]) => body("fpr").append(row([
      name === "semeval_none" ? "SemEval paragraphs labelled “no technique” (human-labelled)" : nice(name),
      num(f.paragraphs), `${num(f.flagged_any)} (${pct(f.fpr_any)})`, `${num(f.flagged_techniques)} (${pct(f.fpr_techniques)})`,
      num(f.flags_per_1000_words)])));
    const sym = r.symmetry || {};
    $("sym").textContent = sym.pairs
      ? `${sym.flipped} of ${sym.pairs} mirrored pairs (same text with the sides swapped) got different labels: flip rate ${pct(sym.flip_rate)}. Lower is better.`
      : "No symmetry pairs in this run.";
    const c = r.cost || {};
    [["Analyses", num(c.analyses)], ["LLM calls", num(c.llm_calls)], ["Mean tokens per analysis", num(c.mean_tokens_per_analysis)],
      ["Mean latency", `${num(c.mean_latency_s)} s`], ["Credits (router) per analysis", num(c.mean_credits_per_analysis)], ["Validator rejection rate (AI quotes not found verbatim)", pct(r.validator_rejection_rate)],
      ["Abstention rate (nothing flagged)", pct(r.abstention_rate)], ["AI failures (fell back to rules)", num(r.llm_failures)]]
      .forEach((x) => body("cost").append(row(x)));
  }

  const ci = (c) => (c ? ` [${pct(c[0])}–${pct(c[1])}]` : "");
  const frac = (o) => (o && o.paragraphs !== undefined ? `${num(o.flagged)}/${num(o.paragraphs)} (${pct(o.rate)})${ci(o.ci)}` : "–");

  function renderCalibration(c) {
    if (!c || !c.levels) return;
    $("calib-card").hidden = false;
    $("calib-info").textContent = `Model ${c.model}, English dev split. Targets for technique false positives on the ` +
      "human-labelled SemEval “no technique” paragraphs: strict at most 5%, balanced at most 15%, broad = no confidence " +
      "filter. Each level uses the lowest confidence threshold that meets its target and still differs from the looser " +
      "level. Claim labels (amber) are not counted on SemEval, which does not annotate claims; on the synthetic hard " +
      `negatives every flag counts. The site default is ${c.default_level}. Thresholds were chosen and measured on the ` +
      "same small set, so these numbers are optimistic and the intervals are wide; the locked test split is the check.";
    ["strict", "balanced", "broad"].forEach((lvl) => {
      const v = c.levels[lvl];
      if (!v) return;
      const a = v.scores.all;
      const sym = v.symmetry || {}, self = v.self_flip || {};
      body("calib").append(row([lvl + (lvl === c.default_level ? " (default)" : ""),
        v.min_confidence ? String(v.min_confidence) : "none",
        `${pct(a.precision)}${ci(a.precision_ci)}`, `${pct(a.recall)}${ci(a.recall_ci)}`, pct(a.f1),
        frac(v.fpr_semeval_none), frac(v.fpr_synthetic_hard_negative),
        sym.pairs ? `${sym.flipped}/${sym.pairs}` : "–", self.texts ? `${self.flipped}/${self.texts}` : "–"]));
    });
  }

  function renderSelf(s, latest) {
    if (!s || !s.broad || !latest || !latest.symmetry) return;
    const sym = latest.symmetry, self = s.broad;
    $("sym").textContent += ` Noise baseline: the same ${self.texts} texts analysed twice gave different labels ` +
      `${self.flipped} times (self-flip rate ${pct(self.self_flip_rate)}). Symmetry flips clearly above the self-flip ` +
      "rate would point to a lean; at or below it they look like noise. With so few pairs, neither can be ruled out.";
  }

  function renderBakeoff(b) {
    if (!b || !(b.models || []).length) return;
    $("bakeoff-wrap").hidden = false;
    const c = b.counts || {};
    $("bakeoff-info").textContent = `Every model saw the same fixed subset: ${num(c.items)} English dev paragraphs ` +
      `(${Object.entries(c.by_source || {}).map(([s, n]) => `${s} ${n}`).join(", ")}; clean: ` +
      `${Object.entries(c.clean_sets || {}).map(([s, n]) => `${nice(s)} ${n}`).join(", ") || "none"}) ` +
      `plus ${num(c.symmetry_pairs)} mirrored pairs. Strict scoring, sensitivity ${b.sensitivity}. ` +
      `A small subset, so differences of a few points are noise. Commit ${b.git_commit} · ${b.timestamp}.`;
    b.models.forEach((m) => body("bakeoff").append(row([m.model, pct(m.precision), pct(m.recall), pct(m.f1),
      pct(m.fpr_semeval_none), pct(m.symmetry_flip_rate), num(m.mean_credits_per_analysis),
      num(m.mean_tokens_per_analysis), `${num(m.mean_latency_s)} s`, num(m.llm_failures)])));
  }

  fetch("/api/metrics").then((r) => r.json()).then(render).catch(() => {
    $("no-results").hidden = false;
  });
})();

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
      row([l.name, l.evaluated ? "Measured" : "Not measured (strict sensitivity by default)"])));

    const latest = data.latest;
    $("no-results").hidden = !!latest;
    $("results").hidden = !latest;
    if (latest) renderLatest(latest);

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
    if ((r.bakeoff || []).length) {
      $("bakeoff-wrap").hidden = false;
      r.bakeoff.forEach((b) => body("bakeoff").append(row([b.model, pct(b.precision), pct(b.recall), pct(b.f1),
        pct(b.fpr_semeval_none), num(b.mean_tokens_per_analysis), `${num(b.mean_latency_s)} s`])));
    }
  }

  fetch("/api/metrics").then((r) => r.json()).then(render).catch(() => {
    $("no-results").hidden = false;
  });
})();

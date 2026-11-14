// /swarm page (vanilla JS). Data: POST /api/swarm. All user data is inserted as text, never as HTML.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const VERDICT = {
    likely_coordinated: { text: "Likely coordinated", cls: "v-likely", ico: "▲" },
    worth_watching: { text: "Worth watching", cls: "v-watch", ico: "●" },
    organic_looking: { text: "Looks organic", cls: "v-organic", ico: "○" },
    insufficient_data: { text: "Not enough data", cls: "v-none", ico: "–" },
  };
  const FAMILY = { T: "Text reuse", S: "Timing", A: "Accounts", M: "Amplification" };
  let last = null;

  const el = (tag, attrs = {}, text) => {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => (k === "class" ? (e.className = v) : e.setAttribute(k, v)));
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const fmtTime = (t) => new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const list = (items, cls) => {
    const ul = el("ul", { class: cls || "" });
    items.forEach((x) => ul.append(el("li", {}, x)));
    return ul;
  };

  function timeline(c) {
    const pts = c.timeline;
    const authors = [...new Set(pts.map((p) => p.author))];
    const rows = Math.min(authors.length, 24);
    const W = 640, padL = 8, padR = 8, rowH = 9, H = rows * rowH + 34;
    const t0 = c.time_span.start, t1 = c.time_span.end, span = Math.max(t1 - t0, 1);
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "timeline");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Timeline: ${pts.length} posts by ${authors.length} accounts from ${fmtTime(t0)} to ${fmtTime(t1)}`);
    const line = document.createElementNS(SVGNS, "line");
    Object.entries({ x1: padL, x2: W - padR, y1: H - 22, y2: H - 22, class: "axis" }).forEach(([k, v]) => line.setAttribute(k, v));
    svg.append(line);
    pts.forEach((p) => {
      const row = authors.indexOf(p.author) % rows;
      const dot = document.createElementNS(SVGNS, "circle");
      dot.setAttribute("cx", padL + ((p.t - t0) / span) * (W - padL - padR));
      dot.setAttribute("cy", 6 + row * rowH);
      dot.setAttribute("r", 3.2);
      dot.setAttribute("class", "dot");
      const title = document.createElementNS(SVGNS, "title");
      title.textContent = `${p.author} · ${fmtTime(p.t)}`;
      dot.append(title);
      svg.append(dot);
    });
    [[padL, "start", fmtTime(t0)], [W - padR, "end", fmtTime(t1)]].forEach(([x, anchor, label]) => {
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", x); t.setAttribute("y", H - 6); t.setAttribute("text-anchor", anchor); t.setAttribute("class", "tl-label");
      t.textContent = label;
      svg.append(t);
    });
    return svg;
  }

  function card(c) {
    const v = VERDICT[c.verdict];
    const art = el("article", { class: `card cluster ${v.cls}`, "aria-labelledby": `h-${c.id}` });
    const head = el("div", { class: "cluster-head" });
    head.append(el("span", { class: `verdict ${v.cls}` }, `${v.ico} ${v.text}`));
    if (c.verdict !== c.verdict_before_innocent_check) {
      head.append(el("span", { class: "muted small" }, `lowered from “${VERDICT[c.verdict_before_innocent_check].text}” by an innocent explanation`));
    }
    art.append(head);
    art.append(el("h3", { id: `h-${c.id}` }, `${c.id.toUpperCase()}: ${c.headline}`));
    const fam = el("p", { class: "families small" });
    Object.entries(FAMILY).forEach(([k, name]) => {
      const on = c.families.includes(k);
      fam.append(el("span", { class: `fam ${on ? "on" : "off"}` }, `${on ? "✓" : "✗"} ${name}`));
    });
    art.append(fam);
    art.append(el("blockquote", { class: "rep" }, c.representative_text));
    art.append(timeline(c));
    const grid = el("div", { class: "evidence" });
    const pro = el("div");
    pro.append(el("h4", {}, "Evidence for coordination"));
    pro.append(c.evidence_for.length ? list(c.evidence_for) : el("p", { class: "muted small" }, "None beyond the shared text."));
    const con = el("div");
    con.append(el("h4", {}, "Evidence against"));
    con.append(c.evidence_against.length ? list(c.evidence_against) : el("p", { class: "muted small" }, "Nothing found."));
    grid.append(pro, con);
    art.append(grid);
    if (c.innocent_explanations.length) {
      const box = el("div", { class: "innocent" });
      box.append(el("h4", {}, "Possible innocent explanations"));
      box.append(list(c.innocent_explanations));
      art.append(box);
    }
    if (c.techniques) {
      art.append(el("p", { class: "small" }, c.techniques.length
        ? `Techniques in the shared text: ${c.techniques.map((t) => t.replace(/_/g, " ")).join(", ")}.`
        : "No manipulation technique found in the shared text."));
    }
    const det = el("details");
    det.append(el("summary", {}, `Accounts (${c.authors.length})`));
    const wrap = el("div", { class: "table-wrap" });
    const table = el("table");
    const thead = el("thead");
    const hr = el("tr");
    ["Account", "Role", "Posts in group", "Posts in batch", "First post", "Name pattern"].forEach((h) => hr.append(el("th", { scope: "col" }, h)));
    thead.append(hr);
    const tb = el("tbody");
    c.authors.forEach((a) => {
      const tr = el("tr");
      [a.author, a.role, a.posts_in_cluster, a.posts_in_batch, fmtTime(a.first_post), a.name_pattern ? "shared pattern" : ""]
        .forEach((x, i) => tr.append(el(i === 0 ? "th" : "td", i === 0 ? { scope: "row" } : {}, String(x))));
      tb.append(tr);
    });
    table.append(thead, tb);
    wrap.append(table);
    det.append(wrap);
    art.append(det);
    return art;
  }

  function render(r) {
    last = r;
    $("result").hidden = false;
    const s = r.summary, bv = s.by_verdict;
    $("summary").textContent = `${s.posts} posts from ${s.authors} accounts (${s.rejected} rows skipped). ` +
      `${s.clusters} groups with repeated text: ${bv.likely_coordinated} likely coordinated, ${bv.worth_watching} worth watching, ` +
      `${bv.organic_looking} look organic, ${bv.insufficient_data} with too few accounts. Took ${(r.elapsed_ms / 1000).toFixed(1)} s.`;
    const notes = $("notes");
    notes.replaceChildren(...r.notes.map((n) => el("li", {}, n)));
    if (r.cost && (r.cost.embeddings || r.cost.llm_calls)) {
      notes.append(el("li", {}, `AI use: ${r.cost.embeddings} embeddings, ${r.cost.llm_calls} tagging calls.`));
    }
    $("rejected-wrap").hidden = !r.rejected.length;
    $("rejected-sum").textContent = `Skipped rows (${s.rejected})`;
    $("rejected").replaceChildren(...r.rejected.map((x) => el("li", {}, `Row ${x.row}: ${x.reason}`)));
    const box = $("clusters");
    box.replaceChildren();
    if (!r.clusters.length) {
      box.append(el("p", { class: "card" }, "No groups of accounts posting the same text were found. That is a valid result: nothing here looks coordinated."));
    }
    r.clusters.forEach((c) => box.append(card(c)));
  }

  async function submit(ev) {
    ev.preventDefault();
    const data = $("data").value;
    if (!data.trim()) {
      $("status").textContent = "Paste or upload some posts first, or load the sample dataset.";
      return;
    }
    $("go").disabled = true;
    $("status").textContent = "Analyzing…";
    try {
      const res = await fetch("/api/swarm", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data, deep: $("deep").checked, tag_clusters: $("tag").checked }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `The server answered ${res.status}.`);
      $("status").textContent = "";
      render(body);
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      $("status").textContent = e.message || "Something went wrong.";
    } finally {
      $("go").disabled = false;
    }
  }

  $("swarm-form").addEventListener("submit", submit);
  $("load-sample").addEventListener("click", async () => {
    const r = await fetch("/static/swarm_sample.csv");
    $("data").value = await r.text();
    $("file-info").textContent = "Sample loaded: fictional posts with two campaigns, a shared news headline and an event hashtag.";
  });
  $("file").addEventListener("change", async () => {
    const f = $("file").files[0];
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) {
      $("file-info").textContent = "That file is larger than 2 MB.";
      return;
    }
    $("data").value = await f.text();
    $("file-info").textContent = `${f.name} loaded.`;
  });
  $("download").addEventListener("click", () => {
    if (!last) return;
    const blob = new Blob([JSON.stringify(last, null, 2)], { type: "application/json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `stoprop-swarm-${last.id}.json` });
    document.body.append(a);
    a.click();
    a.remove();
  });
})();

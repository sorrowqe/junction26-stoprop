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
  const PLATFORM = { bluesky: "Bluesky", mastodon: "Mastodon", youtube: "YouTube", mixed: "several platforms" };
  const TABS = ["link", "links", "file"];
  let last = null;
  let example = null;   // source info of a loaded example (shown with the result)

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
  const status = (msg) => { $("status").textContent = msg || ""; };

  // ------------------------------------------------------------------ tabs
  function activeTab() {
    return TABS.find((t) => $(`tab-${t}`).getAttribute("aria-selected") === "true");
  }
  function selectTab(name, focus) {
    TABS.forEach((t) => {
      const on = t === name;
      $(`tab-${t}`).setAttribute("aria-selected", String(on));
      $(`tab-${t}`).tabIndex = on ? 0 : -1;
      $(`panel-${t}`).hidden = !on;
    });
    if (focus) $(`tab-${name}`).focus();
  }
  TABS.forEach((t, i) => {
    $(`tab-${t}`).addEventListener("click", () => selectTab(t));
    $(`tab-${t}`).addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { e.preventDefault(); selectTab(TABS[(i + d + TABS.length) % TABS.length], true); }
    });
  });

  // --------------------------------------------------------------- results
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
      dot.setAttribute("class", `dot${p.variant ? ` var-${((p.variant - 1) % 4) + 1}` : ""}`);
      const title = document.createElementNS(SVGNS, "title");
      title.textContent = `${p.author} · ${fmtTime(p.t)}${p.variant ? ` · message ${p.variant}` : ""}`;
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
    if (c.kind === "campaign") head.append(el("span", { class: "pill" }, `Campaign: ${c.variants.length} messages`));
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
    if (c.kind === "campaign") {
      const ol = el("ol", { class: "variants" });
      c.variants.forEach((m, i) => {
        const li = el("li", { class: `var-${(i % 4) + 1}` });
        li.append(el("blockquote", { class: "rep" }, m.representative_text));
        li.append(el("p", { class: "small muted" }, `${m.headline} ${VERDICT[m.verdict].text}.`));
        ol.append(li);
      });
      art.append(el("h4", {}, "Message variants"), ol);
    } else {
      art.append(el("blockquote", { class: "rep" }, c.representative_text));
    }
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

  function renderSource(src) {
    const box = $("source");
    box.replaceChildren();
    box.hidden = !src;
    if (!src) return;
    box.className = "source-box";
    const name = PLATFORM[src.platform] || src.platform;
    if (src.root) {
      const p = el("p", { class: "small" });
      p.append(el("strong", {}, `${name} post by ${src.root.author || "unknown"}`));
      if (src.root.created) p.append(document.createTextNode(` · ${src.root.created.slice(0, 16).replace("T", " ")} UTC`));
      if (src.root.url && /^https:\/\//.test(src.root.url)) {
        p.append(document.createTextNode(" · "));
        p.append(el("a", { href: src.root.url, rel: "noopener noreferrer", target: "_blank" }, "open original"));
      }
      box.append(p, el("blockquote", { class: "rep" }, src.root.text || ""));
    }
    const bits = [];
    if (src.posts !== undefined) bits.push(`Collected ${src.posts} posts from ${src.accounts} accounts on ${name}` +
      (src.requests ? ` with ${src.requests} requests` : "") + ".");
    if (src.complete === false) bits.push("Collection was incomplete (see notes).");
    if (src.note) bits.push(src.note);
    if (src.captured_at) bits.push(`Captured ${src.captured_at.slice(0, 10)}.`);
    box.append(el("p", { class: "small muted" }, bits.join(" ")));
  }

  function render(r) {
    last = r;
    $("result").hidden = false;
    renderSource(r.source || example);
    const s = r.summary, bv = s.by_verdict;
    const camp = s.campaigns ? ` (${s.message_clusters} message groups, ${s.campaigns} merged into campaigns)` : "";
    $("summary").textContent = `${s.posts} posts from ${s.authors} accounts${s.rejected ? ` (${s.rejected} rows skipped)` : ""}. ` +
      `${s.clusters} groups with repeated text${camp}: ${bv.likely_coordinated} likely coordinated, ${bv.worth_watching} worth watching, ` +
      `${bv.organic_looking} look organic, ${bv.insufficient_data} with too few accounts. Analysis took ${(r.elapsed_ms / 1000).toFixed(1)} s.`;
    const notes = $("notes");
    notes.replaceChildren(...r.notes.map((n) => el("li", {}, n)));
    if (r.cost && (r.cost.embeddings || r.cost.llm_calls)) {
      notes.append(el("li", {}, `AI use: ${r.cost.embeddings} embeddings, ${r.cost.llm_calls} tagging calls.`));
    }
    const failed = (r.source && r.source.failed) || [];
    $("failed-wrap").hidden = !failed.length;
    $("failed-sum").textContent = `Links that could not be read (${failed.length})`;
    $("failed").replaceChildren(...failed.map((f) => el("li", {}, `${f.url}: ${f.reason}`)));
    $("rejected-wrap").hidden = !r.rejected.length;
    $("rejected-sum").textContent = `Skipped rows (${s.rejected})`;
    $("rejected").replaceChildren(...r.rejected.map((x) => el("li", {}, `Row ${x.row}: ${x.reason}`)));
    const box = $("clusters");
    box.replaceChildren();
    if (!r.clusters.length) {
      box.append(el("p", { class: "card" }, "No groups of accounts posting the same text were found. That is a valid result: " +
        "nothing here looks coordinated."));
    }
    r.clusters.forEach((c) => box.append(card(c)));
  }

  // ---------------------------------------------------------------- submit
  async function post(payload, streaming) {
    const res = await fetch("/api/swarm", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!streaming || !res.ok || !(res.headers.get("content-type") || "").includes("ndjson")) {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `The server answered ${res.status}.`);
      return body;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        if (msg.progress) status(msg.progress);
        if (msg.error) throw new Error(msg.error.detail);
        if (msg.result) return msg.result;
      }
      if (done) throw new Error("The connection closed before the result arrived. Please try again.");
    }
  }

  async function submit(ev) {
    if (ev) ev.preventDefault();
    const opts = { deep: $("deep").checked, tag_clusters: $("tag").checked };
    const tab = activeTab();
    let payload;
    if (tab === "link") {
      const url = $("link").value.trim();
      if (!url) return status("Paste a link to a public post first, or try an example.");
      payload = { url, stream: true, ...opts };
      example = null;
    } else if (tab === "links") {
      const urls = $("links").value.split(/\s+/).map((u) => u.trim()).filter(Boolean);
      if (!urls.length) return status("Paste one or more links first.");
      payload = { urls, stream: true, ...opts };
      example = null;
    } else {
      const data = $("data").value;
      if (!data.trim()) return status("Choose or paste a file first, or try an example.");
      payload = { data, ...opts };
    }
    $("go").disabled = true;
    status(tab === "file" ? "Analyzing…" : "Collecting posts…");
    try {
      const body = await post(payload, tab !== "file");
      status("");
      render(body);
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      status(e.message || "Something went wrong.");
    } finally {
      $("go").disabled = false;
    }
  }

  $("swarm-form").addEventListener("submit", submit);
  $("ex-sample").addEventListener("click", async () => {
    selectTab("file");
    $("data").value = await (await fetch("/static/swarm_sample.csv")).text();
    $("file-info").textContent = "Example loaded: fictional posts with two campaigns, a shared news headline and an event hashtag.";
    example = { platform: "example", note: "Fictional example data made for this demo." };
    submit();
  });
  $("ex-bsky").addEventListener("click", async () => {
    selectTab("file");
    const text = await (await fetch("/static/examples/bluesky_thread.json")).text();
    $("data").value = text;
    const snap = JSON.parse(text).source;
    $("file-info").textContent = "Example loaded: a stored snapshot of a real public Bluesky thread (no live fetch).";
    example = { ...snap, posts: undefined };
    submit();
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
    example = null;
  });
  $("download").addEventListener("click", () => {
    if (!last) return;
    const blob = new Blob([JSON.stringify(last, null, 2)], { type: "application/json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `stoprop-swarm-${last.id}.json` });
    document.body.append(a);
    a.click();
    a.remove();
  });
  fetch("/api/health").then((r) => r.json()).then((h) => {
    const yt = h.collectors && h.collectors.youtube;
    $("yt-status").textContent = yt ? "" : "(not configured on this server right now)";
  }).catch(() => {});
})();

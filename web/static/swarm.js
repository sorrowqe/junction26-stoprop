// Unified /swarm page (vanilla JS): one smart input box for social threads/channels (POST /api/swarm)
// and cross-platform narrative/article spread (POST /api/trace). All external data is inserted as text only.
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
  const TRACE_STATUS = {
    searched: "Searched", not_configured: "Not configured", not_available: "Not available",
    failed: "Failed", partial: "Partly searched",
  };
  const FAMILY = { T: "Text reuse", S: "Timing", A: "Accounts", M: "Amplification" };
  const PLATFORM = {
    bluesky: "Bluesky", mastodon: "Mastodon", youtube: "YouTube",
    reddit: "Reddit", telegram: "Telegram", hackernews: "Hacker News", mixed: "several platforms",
  };
  let last = null;
  let example = null;
  let uploadedData = "";

  const el = (tag, attrs = {}, text) => {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => (k === "class" ? (e.className = v) : e.setAttribute(k, v)));
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const fmtTime = (t) => (t ? new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "date unknown");
  const safeLink = (url, text) => (/^https?:\/\//.test(url || "")
    ? el("a", { href: url, rel: "noopener noreferrer nofollow", target: "_blank" }, text || url)
    : el("span", {}, text || ""));
  const list = (items, cls) => {
    const ul = el("ul", { class: cls || "" });
    items.forEach((x) => ul.append(el("li", {}, x)));
    return ul;
  };
  const setTableRows = (id, data) => $(id).querySelector("tbody").replaceChildren(...data.map((cells) => {
    const tr = el("tr");
    cells.forEach((c, i) => {
      const td = el(i === 0 ? "th" : "td", i === 0 ? { scope: "row" } : {});
      if (c instanceof Node) td.append(c); else td.textContent = String(c);
      tr.append(td);
    });
    return tr;
  }));
  const status = (msg, isErr) => {
    $("status").className = isErr ? "status error" : "status";
    $("status").textContent = msg || "";
  };

  function socialPlatform(url) {
    const u = (url || "").trim();
    if (!/^https?:\/\//i.test(u)) return null;
    if (/^https?:\/\/(?:www\.)?bsky\.app\/profile\/[^/?#]+\/post\/[a-z0-9]+/i.test(u)) return "Bluesky";
    if (/^https?:\/\/(?:(?:www|old|new|np)\.)?reddit\.com\/(?:r\/[a-z0-9_]+(?:\/comments\/[a-z0-9]+)?|comments\/[a-z0-9]+)/i.test(u)
        || /^https?:\/\/redd\.it\/[a-z0-9]+/i.test(u)) return "Reddit";
    if (/^https?:\/\/(?:www\.)?(?:t\.me|telegram\.(?:me|dog))\/(?:s\/)?[a-z0-9_]{4,64}/i.test(u)) return "Telegram";
    if (/^https?:\/\/(?:(?:www|m|music)\.)?youtube\.com\/(?:watch\?.*v=|shorts\/|live\/|embed\/)/i.test(u)
        || /^https?:\/\/youtu\.be\/[a-z0-9_-]{11}/i.test(u)) return "YouTube";
    if (/^https?:\/\/news\.ycombinator\.com\/item\?.*id=\d+/i.test(u)) return "Hacker News";
    if (/^https:\/\/[a-z0-9.-]+\.[a-z]{2,}\/(?:(?:deck\/)?@[^/?#]+|users\/[^/?#]+\/statuses|web\/statuses)\/\d+/i.test(u)) return "Mastodon";
    return null;
  }

  function detectMode() {
    if (uploadedData.trim()) {
      return { kind: "file", hint: "Dataset file loaded — will check all rows for coordinated groups." };
    }
    const raw = $("swarm-input").value.trim();
    if (!raw) {
      return {
        kind: "empty",
        hint: "Auto-detects what you paste: social thread/channel → checks replies & posts; article link or text → traces copies across Web, Reddit, Hacker News & Bluesky.",
      };
    }
    const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const allUrls = lines.length >= 1 && lines.every((l) => /^https?:\/\/\S+$/i.test(l));
    if (allUrls && lines.length === 1) {
      const url = lines[0];
      const plat = socialPlatform(url);
      if (plat) {
        return { kind: "swarm_url", url, hint: `Detected ${plat} link — will collect posts & comments and check for coordinated groups.` };
      }
      return { kind: "trace_url", url, hint: "Detected web/article link — will extract its text and trace where copies spread across the Open Web, Reddit, Hacker News & Bluesky." };
    }
    if (allUrls && lines.length > 1) {
      const socialCount = lines.filter((u) => socialPlatform(u)).length;
      if (socialCount > 0) {
        return { kind: "swarm_urls", urls: lines, hint: `Detected ${lines.length} links — will collect across all channels/posts and look for cross-account coordination.` };
      }
      return { kind: "trace_url", url: lines[0], hint: "Detected article link — will extract its text and trace where copies spread across the web." };
    }
    return { kind: "trace_text", text: raw, hint: "Detected text / claim — will search the Open Web, Reddit, Hacker News & Bluesky for matching posts and check for coordinated spread." };
  }

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

  function resetView() {
    $("verdict-line").hidden = true;
    $("first-seen").hidden = true;
    $("trace-timeline").hidden = true;
    $("trace-extra").hidden = true;
    $("failed-wrap").hidden = true;
    $("rejected-wrap").hidden = true;
    $("trace-root-btn").hidden = true;
    $("clusters").replaceChildren();
  }

  function renderSwarm(r) {
    last = r;
    $("result").hidden = false;
    resetView();
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
    const rootText = r.source && r.source.root && r.source.root.text ? r.source.root.text.trim() : "";
    if (rootText.split(/\s+/).length >= 4) {
      const btn = $("trace-root-btn");
      btn.hidden = false;
      btn.onclick = () => {
        uploadedData = "";
        $("swarm-input").value = rootText.slice(0, 600);
        updateHint();
        submit();
      };
    }
    const failed = (r.source && r.source.failed) || [];
    $("failed-wrap").hidden = !failed.length;
    $("failed-sum").textContent = `Links that could not be read (${failed.length})`;
    $("failed").replaceChildren(...failed.map((f) => el("li", {}, `${f.url}: ${f.reason}`)));
    $("rejected-wrap").hidden = !r.rejected.length;
    $("rejected-sum").textContent = `Skipped rows (${s.rejected})`;
    $("rejected").replaceChildren(...r.rejected.map((x) => el("li", {}, `Row ${x.row}: ${x.reason}`)));
    const box = $("clusters");
    if (!r.clusters.length) {
      box.append(el("p", { class: "card" }, "No multi-account groups posting the same text were found in this batch. " +
        "Nothing here looks coordinated (if this is a single broadcast channel, paste 2+ channel links or click “Trace where this post spread” above)."));
    }
    r.clusters.forEach((c) => box.append(card(c)));
  }

  function drawTraceTimeline(points) {
    const svg = $("trace-timeline");
    svg.replaceChildren();
    svg.hidden = points.length < 2;
    if (points.length < 2) return;
    const W = 640, H = 70, pad = 10;
    const t0 = points[0].t, t1 = points[points.length - 1].t, span = Math.max(t1 - t0, 1);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const axis = document.createElementNS(SVGNS, "line");
    Object.entries({ x1: pad, x2: W - pad, y1: 40, y2: 40, class: "axis" }).forEach(([k, v]) => axis.setAttribute(k, v));
    svg.append(axis);
    points.forEach((p) => {
      const dot = document.createElementNS(SVGNS, "circle");
      dot.setAttribute("cx", pad + ((p.t - t0) / span) * (W - 2 * pad));
      dot.setAttribute("cy", p.platform === "web" ? 28 : 18);
      dot.setAttribute("r", p.is_source ? 6 : 4);
      dot.setAttribute("class", `dot ${p.is_source ? "var-3" : p.platform === "web" ? "var-2" : ""}`);
      const title = document.createElementNS(SVGNS, "title");
      title.textContent = `${p.platform}: ${p.who} · ${fmtTime(p.t)}`;
      dot.append(title);
      svg.append(dot);
    });
    [[pad, "start", fmtTime(t0)], [W - pad, "end", fmtTime(t1)]].forEach(([x, a, label]) => {
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", x); t.setAttribute("y", H - 8); t.setAttribute("text-anchor", a); t.setAttribute("class", "tl-label");
      t.textContent = label;
      svg.append(t);
    });
  }

  function renderTrace(r) {
    last = r;
    $("result").hidden = false;
    resetView();
    $("source").hidden = true;
    const sp = r.spread;
    const vline = $("verdict-line");
    vline.replaceChildren();
    if (r.verdict && VERDICT[r.verdict]) {
      const v = VERDICT[r.verdict];
      vline.append(el("span", { class: `verdict ${v.cls}` }, `${v.ico} ${v.text}`));
      vline.hidden = false;
    }
    $("summary").textContent = `Found ${sp.copies} confirmed cop${sp.copies === 1 ? "y" : "ies"} across ` +
      `${sp.domains.length} website${sp.domains.length === 1 ? "" : "s"} and ${sp.accounts.length} public social account${sp.accounts.length === 1 ? "" : "s"} ` +
      `(${(r.elapsed_ms / 1000).toFixed(1)} s).`;
    const fs = $("first-seen");
    fs.replaceChildren();
    if (sp.first_seen) {
      fs.hidden = false;
      fs.append("Earliest dated copy found: ", el("strong", {}, sp.first_seen.who), ` (${sp.first_seen.platform}) at ${fmtTime(sp.first_seen.ts)} · `, safeLink(sp.first_seen.url, "open"));
    }
    drawTraceTimeline(sp.timeline || []);
    $("notes").replaceChildren(...(r.notes || []).map((n) => el("li", {}, n)));
    if (r.cluster) $("clusters").append(card(r.cluster));

    $("trace-extra").hidden = false;
    $("domains-card").hidden = !sp.domains.length;
    setTableRows("domains", sp.domains.map((d) => [d.domain, d.pages, fmtTime(d.first),
      (d.receipts || []).map((x) => x.text).filter(Boolean).join(" ") || "–"]));
    $("accounts-card").hidden = !sp.accounts.length;
    setTableRows("accounts", sp.accounts.map((a) => [a.account, a.copies, fmtTime(a.first)]));
    $("fingerprints").replaceChildren(...(r.fingerprints || []).map((f) =>
      el("li", {}, `“${f.sentence}” — ${f.copies} confirmed cop${f.copies === 1 ? "y" : "ies"}`)));
    $("fp-method").textContent = {
      llm: "Phrases chosen by AI and verified word for word against your input.",
      heuristic: "Phrases chosen by word-rarity rule (0 AI calls).",
      mixed: "Phrases chosen by AI + word-rarity rule, verified word for word.",
      query: "Exact query searched directly (0 AI calls).",
    }[r.fingerprint_method] || "";
    setTableRows("coverage", (r.coverage || []).map((x) => [x.source, TRACE_STATUS[x.status] || x.status, x.searches, x.results, x.confirmed]));
    $("coverage-notes").replaceChildren(...(r.coverage || []).filter((x) => x.note).map((x) => el("li", {}, x.note)));
    $("copies-sum").textContent = `All matching results (${(r.copies || []).length})`;
    $("copies").replaceChildren(...(r.copies || []).map((x) => {
      const li = el("li", { class: x.confirmed ? "" : "unconfirmed" });
      li.append(el("strong", {}, x.platform === "web" ? x.domain : x.account),
        document.createTextNode(` · ${fmtTime(x.ts)}${x.confirmed ? "" : " · unconfirmed snippet"} · `),
        safeLink(x.url, "open"), el("div", { class: "muted" }, x.text || ""));
      return li;
    }));
  }

  // ---------------------------------------------------------------- submit
  async function postStream(endpoint, payload, streaming) {
    const res = await fetch(endpoint, {
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
        if (msg.progress) status(msg.progress, false);
        if (msg.error) throw new Error(msg.error.detail);
        if (msg.result) return msg.result;
      }
      if (done) throw new Error("The connection closed before the result arrived. Please try again.");
    }
  }

  function updateHint() {
    $("mode-hint").textContent = detectMode().hint;
  }

  async function submit(ev) {
    if (ev) ev.preventDefault();
    const mode = detectMode();
    if (mode.kind === "empty") return status("Paste a link, several links, or a claim / post text first — or click a Try button above.", true);
    const opts = { deep: $("deep").checked, tag_clusters: $("tag").checked };
    $("go").disabled = true;
    status("Starting…", false);
    try {
      if (mode.kind === "file") {
        const body = await postStream("/api/swarm", { data: uploadedData, ...opts }, false);
        status("", false);
        renderSwarm(body);
      } else if (mode.kind === "swarm_url") {
        example = null;
        const body = await postStream("/api/swarm", { url: mode.url, stream: true, ...opts }, true);
        status("", false);
        renderSwarm(body);
      } else if (mode.kind === "swarm_urls") {
        example = null;
        const body = await postStream("/api/swarm", { urls: mode.urls, stream: true, ...opts }, true);
        status("", false);
        renderSwarm(body);
      } else if (mode.kind === "trace_url") {
        example = null;
        const body = await postStream("/api/trace", { url: mode.url, include_forums: true, stream: true }, true);
        status("", false);
        renderTrace(body);
      } else {
        example = null;
        const body = await postStream("/api/trace", { text: mode.text, include_forums: true, stream: true }, true);
        status("", false);
        renderTrace(body);
      }
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      status(e.message || "Something went wrong.", true);
    } finally {
      $("go").disabled = false;
    }
  }

  $("swarm-input").addEventListener("input", () => {
    if (uploadedData) {
      uploadedData = "";
      $("file-info").textContent = "";
      $("clear-file").hidden = true;
    }
    updateHint();
  });
  $("swarm-form").addEventListener("submit", submit);

  $("ex-campaign").addEventListener("click", async () => {
    $("swarm-input").value = "";
    $("go").disabled = true;
    status("Running coordinated campaign demo…", false);
    try {
      const csv = await (await fetch("/static/swarm_sample.csv")).text();
      example = { platform: "demo", note: "Built-in benchmark dataset: 2 coordinated bot campaigns + organic news sharing & an event hashtag." };
      const opts = { deep: $("deep").checked, tag_clusters: $("tag").checked };
      const body = await postStream("/api/swarm", { data: csv, ...opts }, false);
      status("", false);
      renderSwarm(body);
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      status(e.message || "Could not load demo.", true);
    } finally {
      $("go").disabled = false;
    }
  });

  $("ex-reddit").addEventListener("click", () => {
    uploadedData = "";
    $("swarm-input").value = "https://www.reddit.com/r/europe";
    updateHint();
    submit();
  });

  $("ex-telegram").addEventListener("click", () => {
    uploadedData = "";
    $("swarm-input").value = "https://t.me/euronews_en";
    updateHint();
    submit();
  });

  $("ex-trace").addEventListener("click", () => {
    uploadedData = "";
    $("swarm-input").value = "Russia GPS jamming Baltic flights";
    updateHint();
    submit();
  });

  $("file").addEventListener("change", async () => {
    const f = $("file").files[0];
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) {
      $("file-info").textContent = "That file is larger than 2 MB.";
      return;
    }
    uploadedData = await f.text();
    $("file-info").textContent = `${f.name} ready (${Math.round(f.size / 1024)} KB). Click Investigate.`;
    $("clear-file").hidden = false;
    example = null;
    updateHint();
  });
  $("clear-file").addEventListener("click", () => {
    uploadedData = "";
    $("file").value = "";
    $("file-info").textContent = "";
    $("clear-file").hidden = true;
    updateHint();
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
    if (!yt) $("yt-pill").title = "Needs GOOGLE_API_KEY on server to read YouTube comments";
  }).catch(() => {});
  const q = new URLSearchParams(location.search).get("q");
  if (q) { $("swarm-input").value = q; updateHint(); submit(); }
})();

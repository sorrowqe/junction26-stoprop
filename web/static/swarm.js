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
    reddit: "Reddit", telegram: "Telegram", hackernews: "Hacker News",
    web: "Open Web", source: "Source post", mixed: "several platforms", demo: "Demo dataset",
  };
  const PLAT_COLOR = {
    web: "#1a5fb4",
    reddit: "#d9480f",
    telegram: "#0b7285",
    bluesky: "#3b5bdb",
    hackernews: "#d97706",
    mastodon: "#5f3dc4",
    youtube: "#c92a2a",
    source: "#1e6b34",
  };
  const VARIANT_COLORS = ["var(--red)", "var(--blue)", "var(--amber)", "var(--green)"];
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

  // --------------------------------------------------------------- chart helpers & signal scorecard
  const svgEl = (tag, attrs = {}, text) => {
    const n = document.createElementNS(SVGNS, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtAxisTick(ts, spanS) {
    const d = new Date(ts * 1000);
    const hhmm = d.toISOString().slice(11, 16) + " UTC";
    if (spanS < 30 * 3600) return hhmm;
    const md = `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`;
    if (spanS < 14 * 86400) return `${md} ${d.toISOString().slice(11, 16)}`;
    return `${md}, ${d.getUTCFullYear()}`;
  }
  function fmtRel(sec) {
    const s = Math.max(0, Math.round(sec));
    if (s === 0) return "+0s";
    if (s < 90) return `+${s}s`;
    if (s < 5400) return `+${Math.round(s / 60)}m`;
    if (s < 48 * 3600) return `+${(s / 3600).toFixed(s < 10 * 3600 ? 1 : 0)}h`;
    return `+${Math.round(s / 86400)}d`;
  }
  const truncLabel = (s, n = 20) => (s && s.length > n ? s.slice(0, n - 1) + "…" : (s || ""));

  function signalCards(c) {
    const sig = c.signals || {};
    const tPairs = (sig.T && sig.T.pairs) || {};
    const tVal = tPairs.trace ? `${c.post_count} verbatim matches`
      : tPairs.exact ? `${c.post_count} posts · exact copy`
      : tPairs.near ? `${c.post_count} posts · ≥60% overlap`
      : `${c.post_count} posts · paraphrase`;
    const sSig = sig.S || {};
    const sVal = sSig.present
      ? `⚡ ${sSig.posts_in_window}/${c.post_count} in ${fmtRel(sSig.window_s).slice(1)} (exp ${sSig.expected})`
      : (sSig.window_s !== undefined ? `Normal pace (${fmtRel(c.time_span.seconds).slice(1)} span)` : "Normal pace");
    const aSig = sig.A || {};
    const aBits = [];
    if (aSig.usernames && aSig.usernames.present) aBits.push(`${aSig.usernames.group.length} similar names`);
    if (aSig.creation && aSig.creation.present) aBits.push(`${aSig.creation.within_7_days} created ≤7d`);
    if (aSig.single_purpose && aSig.single_purpose.present) aBits.push(`${aSig.single_purpose.authors.length} single-topic`);
    const aVal = aBits.length ? aBits.join(" · ") : "No shared pattern";
    const mSig = sig.M || {};
    const topPush = (mSig.pushed && mSig.pushed[0]) || null;
    const mVal = topPush
      ? `${topPush.authors} pushed ${topPush.kind === "hashtag" ? "#" : topPush.kind === "mention" ? "@" : ""}${truncLabel(topPush.value, 22)}`
      : "No pushed link/tag";

    const grid = el("div", { class: "sig-grid" });
    [
      ["T", "Text reuse", true, tVal],
      ["S", "Timing burst", c.families.includes("S"), sVal],
      ["A", "Account pattern", c.families.includes("A"), aVal],
      ["M", "Shared push", c.families.includes("M"), mVal],
    ].forEach(([key, title, on, val]) => {
      const tile = el("div", { class: `sig-tile k-${key.toLowerCase()}${on ? " on" : ""}` });
      tile.append(el("div", { class: "sig-name" }, `${on ? "✓" : "–"} ${title}`), el("div", { class: "sig-val" }, val));
      grid.append(tile);
    });
    return grid;
  }

  function timeline(c) {
    const pts = c.timeline || [];
    const box = el("div", { class: "chart-box" });
    const head = el("div", { class: "chart-head" });
    head.append(el("span", { class: "chart-title" }, "Account posting swimlanes & burst window"));
    const leg = el("div", { class: "chart-legend" });
    const addLeg = (color, label, square) => {
      const item = el("span", { class: "leg-item" });
      item.append(el("span", { class: square ? "leg-swatch" : "leg-dot", style: `background:${color}` }), document.createTextNode(label));
      leg.append(item);
    };
    addLeg("#1e6b34", "★ First poster", false);
    addLeg("var(--fg)", "Amplifier post", false);
    if ((c.burst_windows || []).length) addLeg("var(--red)", "⚡ Burst window", true);
    head.append(leg);
    box.append(head);

    const orderedAuthors = (c.authors || []).map((a) => a.author);
    pts.forEach((p) => { if (!orderedAuthors.includes(p.author)) orderedAuthors.push(p.author); });
    const MAX_LANES = 14;
    const overflow = orderedAuthors.length > MAX_LANES ? orderedAuthors.length - (MAX_LANES - 1) : 0;
    const laneNames = overflow
      ? [...orderedAuthors.slice(0, MAX_LANES - 1), `+${overflow} other accounts`]
      : orderedAuthors;
    const patSet = new Set((c.authors || []).filter((a) => a.name_pattern).map((a) => a.author));

    const W = 760, padL = 168, padR = 18, padTop = (c.burst_windows || []).length ? 26 : 12, rowH = 21;
    const plotW = W - padL - padR;
    const plotBottom = padTop + laneNames.length * rowH;
    const H = plotBottom + 38;
    const t0 = c.time_span.start, t1 = c.time_span.end, span = Math.max(t1 - t0, 1);
    const xOf = (t) => padL + Math.max(0, Math.min(1, (t - t0) / span)) * plotW;

    const svg = svgEl("svg", {
      viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
      "aria-label": `Account swimlane chart: ${pts.length} posts across ${orderedAuthors.length} accounts`,
    });

    [0, 0.25, 0.5, 0.75, 1].forEach((f, idx) => {
      const x = padL + f * plotW;
      svg.append(svgEl("line", { x1: x, x2: x, y1: padTop, y2: plotBottom, class: "grid-line" }));
      const anchor = idx === 0 ? "start" : idx === 4 ? "end" : "middle";
      svg.append(svgEl("text", { x, y: plotBottom + 14, "text-anchor": anchor, class: "ax-rel" }, fmtRel(f * span)));
      if (idx === 0 || idx === 2 || idx === 4) {
        svg.append(svgEl("text", { x, y: plotBottom + 29, "text-anchor": anchor, class: "ax-txt" }, fmtAxisTick(t0 + f * span, span)));
      }
    });

    (c.burst_windows || []).forEach((bw) => {
      const bx1 = xOf(bw.start), bx2 = Math.max(bx1 + 6, xOf(bw.end));
      svg.append(svgEl("rect", { x: bx1, y: padTop - 2, width: bx2 - bx1, height: plotBottom - padTop + 4, rx: 4, class: "burst-band" }));
      const lx = Math.min(W - padR - 4, Math.max(padL + 4, (bx1 + bx2) / 2));
      const anchor = lx > W - 150 ? "end" : lx < padL + 110 ? "start" : "middle";
      svg.append(svgEl("text", { x: lx, y: padTop - 7, "text-anchor": anchor, class: "burst-txt" },
        `⚡ Burst: ${bw.posts} posts in ${fmtRel(bw.window_s).slice(1)} (exp ${bw.expected})`));
    });

    laneNames.forEach((name, idx) => {
      const y = padTop + idx * rowH + rowH / 2;
      svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: y, y2: y, class: "lane-line" }));
      const prefix = idx === 0 ? "★ " : "";
      const suffix = patSet.has(name) ? " ≈" : "";
      const lbl = svgEl("text", {
        x: padL - 8, y: y + 4, "text-anchor": "end",
        class: `lane-txt${idx === 0 ? " lane-first" : ""}`,
      }, prefix + truncLabel(name, 19) + suffix);
      lbl.append(svgEl("title", {}, idx === 0 ? `${name} (first poster)` : `${name}${patSet.has(name) ? " (similar username pattern)" : ""}`));
      svg.append(lbl);
    });
    svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: plotBottom, y2: plotBottom, class: "axis-line" }));

    const inspector = el("div", { class: "chart-inspector" });
    const showPoint = (p, isFirst) => {
      inspector.replaceChildren();
      const role = isFirst ? "★ First poster" : (p.in_burst ? "⚡ Amplifier (in burst window)" : "Amplifier");
      inspector.append(
        el("strong", {}, `${p.author}`),
        document.createTextNode(` · ${role} · ${fmtTime(p.t)} (${fmtRel(p.t - t0)} after first post)`),
      );
      if (p.url && /^https?:\/\//.test(p.url)) {
        inspector.append(document.createTextNode(" · "), safeLink(p.url, "open post ↗"));
      }
      if (p.snippet) inspector.append(el("div", { class: "muted small" }, `“${p.snippet}”`));
    };

    pts.forEach((p, idx) => {
      const aIdx = orderedAuthors.indexOf(p.author);
      const laneIdx = overflow && aIdx >= MAX_LANES - 1 ? MAX_LANES - 1 : Math.max(0, aIdx);
      const cx = xOf(p.t), cy = padTop + laneIdx * rowH + rowH / 2;
      const isFirst = aIdx === 0 && idx === 0;
      const fill = isFirst ? "#1e6b34"
        : p.variant ? VARIANT_COLORS[(p.variant - 1) % VARIANT_COLORS.length]
        : (p.in_burst ? "var(--red)" : "var(--fg)");
      const dot = svgEl("circle", { cx, cy, r: isFirst ? 6 : 4.6, fill, class: "pt-dot", tabindex: "0" });
      dot.append(svgEl("title", {}, `${p.author} · ${fmtTime(p.t)} (${fmtRel(p.t - t0)})`));
      dot.addEventListener("mouseenter", () => showPoint(p, isFirst));
      dot.addEventListener("focus", () => showPoint(p, isFirst));
      dot.addEventListener("click", () => showPoint(p, isFirst));
      svg.append(dot);
    });

    if (pts.length) showPoint(pts[0], true);
    box.append(svg, inspector);
    return box;
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
    art.append(signalCards(c));
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
      const tbox = el("div", { class: "platform-badges", style: "margin:.55rem 0" });
      if (c.techniques.length) {
        tbox.append(el("span", { class: "small muted" }, "AI-detected manipulation tactics in shared text:"));
        c.techniques.forEach((t) => tbox.append(el("span", { class: "pill warn" }, t.replace(/_/g, " "))));
      } else {
        tbox.append(el("span", { class: "pill ok" }, "✓ No manipulation tactics flagged in shared text"));
      }
      art.append(tbox);
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
    const oc = $("overview-chart");
    oc.hidden = true;
    oc.replaceChildren();
    $("trace-extra").hidden = true;
    $("failed-wrap").hidden = true;
    $("rejected-wrap").hidden = true;
    $("trace-root-btn").hidden = true;
    $("clusters").replaceChildren();
  }

  function renderBatchChart(act) {
    const oc = $("overview-chart");
    if (!act || !act.bins || !act.bins.length || act.peak_posts === 0) return;
    oc.hidden = false;
    const hasLikely = act.bins.some((b) => b.likely > 0);
    const hasWatch = act.bins.some((b) => b.watch > 0);
    const head = el("div", { class: "chart-head" });
    head.append(el("span", { class: "chart-title" }, "Posting volume over time (entire collected batch)"));
    const leg = el("div", { class: "chart-legend" });
    const addLeg = (cls, label) => {
      const i = el("span", { class: "leg-item" });
      i.append(el("span", { class: `leg-swatch ${cls}`, style: cls.startsWith("#") || cls.startsWith("var(") ? `background:${cls}` : "" }),
        document.createTextNode(label));
      leg.append(i);
    };
    if (hasLikely) addLeg("var(--red)", "Likely coordinated");
    if (hasWatch) addLeg("var(--amber)", "Worth watching");
    addLeg("var(--muted)", "Other / unclustered posts");
    head.append(leg);
    oc.append(head);

    const W = 760, H = 168, padL = 38, padR = 36, padT = 12, padB = 28;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const maxY = Math.max(act.peak_posts, 2);
    const yOf = (v) => padT + plotH - (v / maxY) * plotH;
    const span = Math.max(act.end - act.start, 1);

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
      "aria-label": `Posting volume histogram: peak ${act.peak_posts} posts per window` });

    [0, Math.round(maxY / 2), maxY].filter((v, i, a) => a.indexOf(v) === i).forEach((v) => {
      const y = yOf(v);
      svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: y, y2: y, class: v === 0 ? "axis-line" : "grid-line" }));
      svg.append(svgEl("text", { x: padL - 6, y: y + 4, "text-anchor": "end", class: "ax-txt" }, v));
    });

    if (act.mean_per_bin > 0 && act.mean_per_bin < maxY) {
      const ym = yOf(act.mean_per_bin);
      svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: ym, y2: ym, class: "avg-line" }));
      svg.append(svgEl("text", { x: W - padR + 4, y: ym + 4, class: "ax-txt" }, `avg ${act.mean_per_bin}`));
    }

    const inspector = el("div", { class: "chart-inspector" });
    const showBin = (b) => {
      inspector.replaceChildren();
      const parts = [];
      if (b.likely) parts.push(`${b.likely} likely coordinated`);
      if (b.watch) parts.push(`${b.watch} worth watching`);
      if (b.other) parts.push(`${b.other} unclustered`);
      inspector.append(
        el("strong", {}, `${fmtAxisTick(b.t0, span)} → ${fmtAxisTick(b.t1, span)}`),
        document.createTextNode(` · ${b.total} post${b.total === 1 ? "" : "s"} from ${b.authors} account${b.authors === 1 ? "" : "s"}` +
          (parts.length ? ` (${parts.join(", ")})` : "")),
      );
    };

    const n = act.bins.length, bw = plotW / n, gap = Math.max(1.5, bw * 0.16);
    let peakBin = act.bins[0];
    act.bins.forEach((b, i) => {
      if (b.total > peakBin.total) peakBin = b;
      const x = padL + i * bw + gap / 2, w = Math.max(2, bw - gap);
      let curY = padT + plotH;
      [["other", "bar-other"], ["watch", "bar-watch"], ["likely", "bar-likely"]].forEach(([k, cls]) => {
        if (!b[k]) return;
        const h = (b[k] / maxY) * plotH;
        curY -= h;
        svg.append(svgEl("rect", { x, y: curY, width: w, height: h, rx: 1.5, class: cls }));
      });
      const hit = svgEl("rect", { x: padL + i * bw, y: padT, width: bw, height: plotH, class: "bar-hit", tabindex: "0" });
      hit.addEventListener("mouseenter", () => showBin(b));
      hit.addEventListener("focus", () => showBin(b));
      hit.addEventListener("click", () => showBin(b));
      svg.append(hit);
    });

    [0, 0.25, 0.5, 0.75, 1].forEach((f, idx) => {
      const x = padL + f * plotW;
      const anchor = idx === 0 ? "start" : idx === 4 ? "end" : "middle";
      svg.append(svgEl("text", { x, y: H - 6, "text-anchor": anchor, class: "ax-txt" }, fmtAxisTick(act.start + f * span, span)));
    });

    showBin(peakBin);
    oc.append(svg, inspector);

    const doms = act.top_domains || [], tags = act.top_hashtags || [];
    if (doms.length || tags.length) {
      const meta = el("div", { class: "chart-meta-pills muted" });
      if (doms.length) {
        meta.append(el("span", {}, "Top shared domains: "));
        doms.forEach((d) => meta.append(el("span", { class: "pill" }, `${d.domain} (${d.posts})`)));
      }
      if (tags.length) {
        meta.append(el("span", { style: doms.length ? "margin-left:.4rem" : "" }, "Top hashtags: "));
        tags.forEach((h) => meta.append(el("span", { class: "pill" }, `#${h.tag} (${h.posts})`)));
      }
      oc.append(meta);
    }
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
    renderBatchChart(s.activity);
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

  function renderSpreadChart(sp) {
    const oc = $("overview-chart");
    const pts = sp.timeline || [];
    if (pts.length < 2) return;
    oc.hidden = false;

    const platOrder = ["web", "reddit", "telegram", "bluesky", "hackernews", "mastodon", "youtube", "source"];
    const countsByPlat = {};
    pts.forEach((p) => { countsByPlat[p.platform] = (countsByPlat[p.platform] || 0) + 1; });
    const lanes = platOrder.filter((k) => countsByPlat[k]);
    pts.forEach((p) => { if (!lanes.includes(p.platform)) lanes.push(p.platform); });

    const head = el("div", { class: "chart-head" });
    head.append(el("span", { class: "chart-title" }, "Spread volume & platform swimlanes (dated matches)"));
    const leg = el("div", { class: "chart-legend" });
    lanes.forEach((pl) => {
      const item = el("span", { class: "leg-item" });
      item.append(el("span", { class: "leg-dot", style: `background:${PLAT_COLOR[pl] || "var(--fg)"}` }),
        document.createTextNode(`${PLATFORM[pl] || pl} (${countsByPlat[pl]})`));
      leg.append(item);
    });
    head.append(leg);
    oc.append(head);

    const W = 760, padL = 138, padR = 20, barTop = 12, barH = 54, laneTop = 82, rowH = 26;
    const plotW = W - padL - padR;
    const plotBottom = laneTop + lanes.length * rowH;
    const H = plotBottom + 32;
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t, span = Math.max(t1 - t0, 60);
    const xOf = (t) => padL + Math.max(0, Math.min(1, (t - t0) / span)) * plotW;

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
      "aria-label": `${pts.length} dated copies across ${lanes.length} platform lanes` });

    const act = sp.activity;
    if (act && act.bins && act.peak > 0) {
      const maxB = Math.max(act.peak, 2);
      svg.append(svgEl("text", { x: padL - 8, y: barTop + 10, "text-anchor": "end", class: "ax-txt" }, `${act.peak} peak`));
      svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: barTop + barH, y2: barTop + barH, class: "axis-line" }));
      const nb = act.bins.length, bw = plotW / nb, gap = Math.max(1.5, bw * 0.18);
      act.bins.forEach((b, i) => {
        if (!b.total) return;
        const x = padL + i * bw + gap / 2, w = Math.max(2, bw - gap);
        let curY = barTop + barH;
        lanes.forEach((pl) => {
          const c = (b.by_platform && b.by_platform[pl]) || 0;
          if (!c) return;
          const h = (c / maxB) * barH;
          curY -= h;
          svg.append(svgEl("rect", { x, y: curY, width: w, height: h, rx: 1.5, fill: PLAT_COLOR[pl] || "var(--fg)", "fill-opacity": "0.78" }));
        });
      });
    }

    [0, 0.25, 0.5, 0.75, 1].forEach((f, idx) => {
      const x = padL + f * plotW;
      svg.append(svgEl("line", { x1: x, x2: x, y1: laneTop, y2: plotBottom, class: "grid-line" }));
      const anchor = idx === 0 ? "start" : idx === 4 ? "end" : "middle";
      svg.append(svgEl("text", { x, y: plotBottom + 18, "text-anchor": anchor, class: "ax-txt" }, fmtAxisTick(t0 + f * span, span)));
    });

    lanes.forEach((pl, idx) => {
      const y = laneTop + idx * rowH + rowH / 2;
      svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: y, y2: y, class: "lane-line" }));
      svg.append(svgEl("text", { x: padL - 8, y: y + 4, "text-anchor": "end", class: "lane-txt" },
        `${PLATFORM[pl] || pl} (${countsByPlat[pl]})`));
    });
    svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: plotBottom, y2: plotBottom, class: "axis-line" }));

    const inspector = el("div", { class: "chart-inspector" });
    const showPt = (p, isEarliest) => {
      inspector.replaceChildren();
      inspector.append(
        el("strong", {}, `[${PLATFORM[p.platform] || p.platform}] ${p.who}`),
        document.createTextNode(` · ${isEarliest ? "★ Earliest dated match · " : ""}${fmtTime(p.t)} (${fmtRel(p.t - t0)})`),
      );
      if (p.url && /^https?:\/\//.test(p.url)) {
        inspector.append(document.createTextNode(" · "), safeLink(p.url, "open ↗"));
      }
      if (p.snippet) inspector.append(el("div", { class: "muted small" }, `“${p.snippet}”`));
    };

    pts.forEach((p, idx) => {
      const laneIdx = Math.max(0, lanes.indexOf(p.platform));
      const jitter = ((idx % 3) - 1) * 3.2;
      const cx = xOf(p.t), cy = laneTop + laneIdx * rowH + rowH / 2 + jitter;
      const isEarliest = idx === 0;
      const dot = svgEl("circle", {
        cx, cy, r: isEarliest ? 6.5 : 4.8,
        fill: PLAT_COLOR[p.platform] || "var(--fg)", class: "pt-dot", tabindex: "0",
      });
      dot.append(svgEl("title", {}, `${PLATFORM[p.platform] || p.platform}: ${p.who} · ${fmtTime(p.t)}`));
      dot.addEventListener("mouseenter", () => showPt(p, isEarliest));
      dot.addEventListener("focus", () => showPt(p, isEarliest));
      dot.addEventListener("click", () => showPt(p, isEarliest));
      svg.append(dot);
    });

    showPt(pts[0], true);
    oc.append(svg, inspector);
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
      fs.append("Earliest dated copy found: ", el("strong", {}, sp.first_seen.who), ` (${PLATFORM[sp.first_seen.platform] || sp.first_seen.platform}) at ${fmtTime(sp.first_seen.ts)} · `, safeLink(sp.first_seen.url, "open"));
    }
    renderSpreadChart(sp);
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
      example = { platform: "demo", note: "Built-in benchmark dataset: 2 coordinated campaigns + organic news sharing & an event hashtag." };
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
    $("swarm-input").value = "https://www.reddit.com/r/worldnews";
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
    $("swarm-input").value = "Federal Reserve interest rate decision";
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
  if ($("copy-swarm-brief")) {
    $("copy-swarm-brief").addEventListener("click", async () => {
      if (!last) return;
      const btn = $("copy-swarm-brief");
      const lines = ["=== Stoprop Swarm & Spread Brief ===", $("summary").textContent];
      if (last.spread && last.spread.first_seen) {
        const f = last.spread.first_seen;
        lines.push(`Earliest dated copy: ${f.who} (${f.platform}) at ${fmtTime(f.ts)} — ${f.url || ""}`);
      }
      const cls = last.clusters || (last.cluster ? [last.cluster] : []);
      cls.slice(0, 5).forEach((c) => {
        lines.push("", `[${VERDICT[c.verdict].text}] ${c.headline}`);
        lines.push(`Sample: “${(c.representative_text || "").slice(0, 180)}”`);
        (c.evidence_for || []).forEach((e) => lines.push(`  + ${e}`));
      });
      lines.push("", "Analyzed via https://stoprop.tech/swarm");
      try {
        await navigator.clipboard.writeText(lines.join("\n"));
        const orig = btn.textContent;
        btn.textContent = "✓ Copied brief";
        setTimeout(() => { btn.textContent = orig; }, 1800);
      } catch { /* ignore */ }
    });
  }
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

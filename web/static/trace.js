// /trace page (vanilla JS). Data: POST /api/trace (NDJSON stream). External data is inserted as text only.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const VERDICT = {
    likely_coordinated: { text: "Likely coordinated", cls: "v-likely", ico: "▲" },
    worth_watching: { text: "Worth watching", cls: "v-watch", ico: "●" },
    organic_looking: { text: "Looks organic", cls: "v-organic", ico: "○" },
    insufficient_data: { text: "Not enough copies to judge", cls: "v-none", ico: "–" },
  };
  const STATUS = { searched: "Searched", not_configured: "Not configured", not_available: "Not available",
    failed: "Failed", partial: "Partly searched" };
  const EXAMPLE = "City hall quietly approved the harbour dredging contract on Tuesday. The consortium will receive " +
    "41 million euros over six years without an open tender. Residents were told about it only after the vote.";
  let last = null;

  const el = (tag, attrs = {}, text) => {
    const e = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => (k === "class" ? (e.className = v) : e.setAttribute(k, v)));
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const when = (t) => (t ? new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "date unknown");
  const link = (url, text) => (/^https?:\/\//.test(url || "")
    ? el("a", { href: url, rel: "noopener noreferrer nofollow", target: "_blank" }, text || url)
    : el("span", {}, text || ""));
  const status = (m) => { $("status").textContent = m || ""; };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const rows = (id, data) => $(id).querySelector("tbody").replaceChildren(...data.map((cells) => {
    const tr = el("tr");
    cells.forEach((c, i) => {
      const td = el(i === 0 ? "th" : "td", i === 0 ? { scope: "row" } : {});
      if (c instanceof Node) td.append(c); else td.textContent = String(c);
      tr.append(td);
    });
    return tr;
  }));

  function drawTimeline(points) {
    const svg = $("timeline");
    svg.replaceChildren();
    svg.style.display = points.length < 2 ? "none" : "";
    if (points.length < 2) return;
    const W = 640, H = 70, pad = 10;
    const t0 = points[0].t, t1 = points[points.length - 1].t, span = Math.max(t1 - t0, 1);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("aria-label", `${points.length} dated copies from ${when(t0)} to ${when(t1)}`);
    const axis = document.createElementNS(SVGNS, "line");
    Object.entries({ x1: pad, x2: W - pad, y1: 40, y2: 40, class: "axis" }).forEach(([k, v]) => axis.setAttribute(k, v));
    svg.append(axis);
    points.forEach((p) => {
      const dot = document.createElementNS(SVGNS, "circle");
      dot.setAttribute("cx", pad + ((p.t - t0) / span) * (W - 2 * pad));
      dot.setAttribute("cy", p.platform === "web" ? 28 : 18);
      dot.setAttribute("r", 4);
      dot.setAttribute("class", `dot ${p.is_source ? "var-3" : p.platform === "web" ? "var-2" : ""}`);
      dot.setAttribute("r", p.is_source ? 6 : 4);
      const title = document.createElementNS(SVGNS, "title");
      title.textContent = `${p.is_source ? "The post you gave us" : p.platform === "web" ? "Website" : p.platform}: ${p.who} · ${when(p.t)}`;
      dot.append(title);
      svg.append(dot);
    });
    [[pad, "start", when(t0)], [W - pad, "end", when(t1)]].forEach(([x, a, label]) => {
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", x); t.setAttribute("y", H - 8); t.setAttribute("text-anchor", a); t.setAttribute("class", "tl-label");
      t.textContent = label;
      svg.append(t);
    });
  }

  function render(r) {
    last = r;
    $("result").hidden = false;
    const sp = r.spread;
    const web = r.copies.filter((c) => c.confirmed && c.platform === "web").length;
    const bsky = r.copies.filter((c) => c.confirmed && c.platform === "bluesky").length;
    $("summary").textContent = sp.copies
      ? `Found ${plural(sp.copies, "copy", "copies")}: ${web} on websites, ${bsky} on Bluesky` +
        `${sp.unconfirmed ? ` (plus ${sp.unconfirmed} web results we could not confirm)` : ""}.`
      : "No copies of these sentences were found in the places we searched. That is a valid result.";
    const fs = sp.first_seen;
    $("first-seen").replaceChildren();
    if (fs) {
      $("first-seen").append(document.createTextNode(`First seen: ${fs.is_source ? "the post you gave us" : fs.who} ` +
        `(${fs.platform === "web" ? "website" : fs.platform}), ${when(fs.ts)} · `), link(fs.url, "open"));
    }
    const vl = $("verdict-line");
    vl.replaceChildren();
    if (r.verdict) {
      const v = VERDICT[r.verdict];
      vl.append(el("span", { class: `verdict ${v.cls}` }, `${v.ico} ${v.text}`));
      if (r.cluster) vl.append(el("span", { class: "small" }, r.cluster.headline));
    }
    drawTimeline(sp.timeline);
    $("notes").replaceChildren(...r.notes.filter((n) => n !== r.principle).map((n) => el("li", {}, n)));

    const c = r.cluster;
    $("evidence-card").hidden = !c;
    if (c) {
      $("ev-for").replaceChildren(...c.evidence_for.map((x) => el("li", {}, x)));
      $("ev-against").replaceChildren(...c.evidence_against.map((x) => el("li", {}, x)));
      $("innocent-wrap").hidden = !c.innocent_explanations.length;
      $("innocent").replaceChildren(...c.innocent_explanations.map((x) => el("li", {}, x)));
    }
    $("fingerprints").replaceChildren(...r.fingerprints.map((f) => el("li", {}, `“${f.query}” (${plural(f.copies, "copy", "copies")})`)));
    $("fp-method").textContent = { llm: "Sentences chosen by AI and checked word for word against your text.",
      heuristic: "Sentences chosen by a word-rarity rule (no AI).",
      mixed: "Sentences chosen by AI and a word-rarity rule, checked word for word." }[r.fingerprint_method] || "";
    rows("coverage", r.coverage.map((x) => [x.source, STATUS[x.status] || x.status, x.searches, x.results, x.confirmed]));
    $("coverage-notes").replaceChildren(...r.coverage.filter((x) => x.note).map((x) => el("li", {}, x.note)));

    $("domains-card").hidden = !sp.domains.length;
    rows("domains", sp.domains.map((d) => [d.domain, d.pages, when(d.first),
      (d.receipts || []).map((x) => x.text).filter(Boolean).join(" ") || "–"]));
    $("accounts-card").hidden = !sp.accounts.length;
    rows("accounts", sp.accounts.map((a) => [a.account, a.copies, when(a.first)]));

    $("copies-sum").textContent = `All search results we kept (${r.copies.length})`;
    $("copies").replaceChildren(...r.copies.map((x) => {
      const li = el("li", { class: x.confirmed ? "" : "unconfirmed" });
      li.append(el("strong", {}, `${x.platform === "web" ? x.domain : x.account}`),
        document.createTextNode(` · ${when(x.ts)}${x.confirmed ? "" : " · not confirmed in the snippet"} · `),
        link(x.url, "open"), el("div", { class: "muted" }, x.text));
      return li;
    }));
  }

  async function submit(ev) {
    if (ev) ev.preventDefault();
    const value = $("input").value.trim();
    if (!value) return status("Paste a post's text or a link first.");
    const payload = /^https?:\/\/\S+$/.test(value) ? { url: value } : { text: value };
    $("go").disabled = true;
    status("Starting…");
    try {
      const res = await fetch("/api/trace", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, stream: true }) });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `The server answered ${res.status}.`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "", result = null;
      while (!result) {
        const { value: chunk, done } = await reader.read();
        if (chunk) buf += dec.decode(chunk, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const msg = JSON.parse(line);
          if (msg.progress) status(msg.progress);
          if (msg.error) throw new Error(msg.error.detail);
          if (msg.result) result = msg.result;
        }
        if (done && !result) throw new Error("The connection closed before the result arrived. Please try again.");
      }
      status("");
      render(result);
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      status(e.message || "Something went wrong.");
    } finally {
      $("go").disabled = false;
    }
  }

  $("trace-form").addEventListener("submit", submit);
  $("ex-text").addEventListener("click", () => { $("input").value = EXAMPLE; $("input").focus(); });
  $("download").addEventListener("click", () => {
    if (!last) return;
    const blob = new Blob([JSON.stringify(last, null, 2)], { type: "application/json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `stoprop-trace-${last.id}.json` });
    document.body.append(a);
    a.click();
    a.remove();
  });
})();

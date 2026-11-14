// /source page: provenance report, domain history timeline SVG, live page transparency, and receipts (no AI cost).
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  let lastTarget = null;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const svgEl = (tag, attrs = {}, text) => {
    const n = document.createElementNS(SVGNS, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
    if (text !== undefined) n.textContent = text;
    return n;
  };

  function renderScorePills(receipts, page) {
    const box = $("score-pills");
    box.replaceChildren();
    let ok = 0, warn = 0, info = 0;
    (receipts || []).forEach((r) => {
      if (r.status === "not_checked" || r.status === "error") return;
      if (r.flag === "ok") ok += 1;
      else if (r.flag === "warning") warn += 1;
      else info += 1;
    });
    if (page && page.brand_warning) warn += 1;
    if (page && page.cross_domain_redirect) warn += 1;
    if (ok) box.append(el("span", "pill ok", `✓ ${ok} reassuring`));
    if (warn) box.append(el("span", "pill warn", `! ${warn} worth a closer look`));
    if (info) box.append(el("span", "pill", `i ${info} neutral records`));
  }

  function renderHistoryChart(milestones) {
    const box = $("history-chart");
    box.replaceChildren();
    if (!milestones || !milestones.length) { box.hidden = true; return; }
    box.hidden = false;

    const nowMs = Date.now();
    const pts = milestones.map((m) => ({ ...m, ms: Date.parse(m.date + "T00:00:00Z") })).filter((m) => !Number.isNaN(m.ms));
    if (!pts.length) { box.hidden = true; return; }
    const oldest = pts[0];
    const oldestDays = Math.max(0, Math.round((nowMs - oldest.ms) / 86400000));
    const ageLabel = oldestDays < 60 ? `${oldestDays} days of public record`
      : oldestDays < 730 ? `~${Math.round(oldestDays / 30.4)} months of public record`
      : `~${Math.floor(oldestDays / 365)} years of public record`;

    const head = el("div", "chart-head");
    head.append(
      el("span", "chart-title", "Domain history timeline"),
      el("span", `pill ${oldestDays < 180 ? "warn" : "ok"}`, oldestDays < 180 ? `! Young site (${ageLabel})` : `Earliest record: ${oldest.date} (${ageLabel})`),
    );
    box.append(head);

    const W = 720, H = 76, padL = 56, padR = 56, yAxis = 38;
    const minMs = Math.min(oldest.ms, nowMs - 90 * 86400000);
    const span = Math.max(1, nowMs - minMs);
    const xOf = (ms) => padL + ((ms - minMs) / span) * (W - padL - padR);

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
      "aria-label": `Domain history from ${oldest.date} to today` });
    svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: yAxis, y2: yAxis, class: "axis-line" }));

    pts.forEach((m, idx) => {
      const x = xOf(m.ms);
      const above = idx % 2 === 0;
      const color = (m.days != null && m.days < 180) ? "var(--amber)" : "var(--blue)";
      svg.append(svgEl("circle", { cx: x, cy: yAxis, r: 5.5, fill: color, class: "pt-dot" }));
      svg.append(svgEl("text", {
        x, y: above ? yAxis - 12 : yAxis + 20,
        "text-anchor": x < 110 ? "start" : x > W - 110 ? "end" : "middle",
        class: "ax-rel",
      }, `${m.label}: ${m.date}`));
    });

    const xNow = W - padR;
    svg.append(svgEl("circle", { cx: xNow, cy: yAxis, r: 4.5, fill: "var(--fg)" }));
    svg.append(svgEl("text", { x: xNow, y: yAxis + 20, "text-anchor": "end", class: "ax-txt" }, "Today"));
    box.append(svg);
  }

  function renderPageInspect(page) {
    const card = $("page-card");
    const alertBox = $("brand-alert");
    const meta = $("page-meta");
    const badges = $("transp-badges");
    alertBox.hidden = true;
    badges.replaceChildren();
    if (!page) { card.hidden = true; return; }
    card.hidden = false;

    if (!page.reachable) {
      meta.textContent = `Could not load the live page directly (${page.reason || "unreachable"}). Domain registry and archive checks still ran below.`;
      return;
    }

    if (page.brand_warning) {
      alertBox.hidden = false;
      alertBox.textContent = `Brand check: ${page.brand_warning.text}`;
    }

    const parts = [];
    if (page.title) parts.push(`Title: “${page.title}”`);
    if (page.is_article) {
      parts.push(page.author ? `Byline: ${page.author}` : "Byline: none detected");
      parts.push(page.published_date ? `Published: ${page.published_date}` : "Date: none detected");
      if (page.word_count) parts.push(`${page.word_count} words`);
    }
    if (page.redirects && page.redirects.length) {
      parts.push(`${page.cross_domain_redirect ? "Cross-domain redirect" : "Redirected"} → ${page.final_domain}`);
    }
    meta.textContent = parts.join(" · ") || `Live page answered at ${page.final_url}`;

    const sig = page.signals || {};
    [
      [sig.https, "HTTPS encrypted", "No HTTPS"],
      [sig.about_link, "About / Masthead link", "No About link found"],
      [sig.contact_link, "Contact info link", "No Contact link found"],
      [sig.policy_link, "Editorial / Privacy policy", "No Policy link found"],
    ].forEach(([on, yes, no]) => {
      badges.append(el("span", `pill ${on ? "ok" : ""}`, `${on ? "✓" : "–"} ${on ? yes : no}`));
    });
    if (page.is_article) {
      badges.append(el("span", `pill ${page.author ? "ok" : "warn"}`, page.author ? "✓ Named author" : "! No author byline detected"));
    }
  }

  async function check(refresh) {
    const target = refresh ? lastTarget : $("target").value.trim();
    const status = refresh ? $("refresh-status") : $("status");
    status.className = refresh ? "small muted" : "status";
    if (!target) {
      status.textContent = "Please enter a link or a domain name.";
      status.className = "status error";
      return;
    }
    const btn = refresh ? $("refresh") : $("check");
    btn.disabled = true;
    status.textContent = refresh ? "Checking again…" : "Inspecting domain, archives, certificates & live page…";
    try {
      const isUrl = /^https?:\/\//i.test(target);
      const body = { refresh: !!refresh };
      if (isUrl) body.url = target; else body.domain = target;
      const r = await fetch("/api/receipts", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Something went wrong (HTTP ${r.status}).`);
      lastTarget = target;
      $("domain").textContent = data.domain;
      const regNote = $("reg-note");
      if (data.registrable_domain && data.registrable_domain !== data.domain) {
        regNote.hidden = false;
        regNote.textContent = `Subdomain of registrable domain ${data.registrable_domain}`;
      } else {
        regNote.hidden = true;
      }
      renderScorePills(data.receipts, data.page);
      renderHistoryChart(data.milestones || []);
      renderPageInspect(data.page);
      window.StopropReceipts.render($("receipts"), data.receipts);

      const aLink = $("analyze-link");
      if (isUrl) {
        aLink.hidden = false;
        aLink.href = `/?url=${encodeURIComponent(target)}`;
      } else {
        aLink.hidden = true;
      }
      $("swarm-link").href = `/swarm?q=${encodeURIComponent(isUrl ? target : data.registrable_domain || target)}`;

      $("result").hidden = false;
      status.textContent = refresh ? "Updated. Each check can be refreshed at most once every 15 minutes." : "";
      try { history.replaceState(null, "", `/source?q=${encodeURIComponent(target)}`); } catch { /* ignore */ }
    } catch (err) {
      status.textContent = err.message || "Could not reach the server.";
      if (!refresh) status.className = "status error";
    } finally {
      btn.disabled = false;
    }
  }

  $("source-form").addEventListener("submit", (e) => { e.preventDefault(); check(false); });
  $("refresh").addEventListener("click", () => check(true));
  document.querySelectorAll("[data-try]").forEach((b) => b.addEventListener("click", () => {
    $("target").value = b.dataset.try;
    check(false);
  }));
  const q = new URLSearchParams(location.search).get("q");
  if (q) { $("target").value = q; check(false); }
})();

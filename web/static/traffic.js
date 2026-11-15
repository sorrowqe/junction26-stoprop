// Clean the traffic (/traffic) — server-log bot, credential-stuffing, spoofed crawler & scraper analyzer.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  let last = null;
  let activeFmt = "htaccess";

  const CAT_LABEL = {
    credential_stuffing: { text: "Credential stuffing", cls: "v-likely", ico: "▲" },
    vuln_scanner: { text: "Vulnerability scanner", cls: "v-likely", ico: "▲" },
    spoofed_crawler: { text: "Spoofed search crawler", cls: "v-likely", ico: "▲" },
    ai_or_bulk_scraper: { text: "AI / bulk scraper", cls: "v-watch", ico: "●" },
    verified_crawler: { text: "Verified / polite crawler", cls: "v-organic", ico: "✓" },
    human_visitor: { text: "Real visitor", cls: "v-organic", ico: "○" },
  };

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
  const fmtTick = (ts) => new Date(ts * 1000).toISOString().slice(11, 19) + " UTC";

  // Realistic built-in demos using public reference IPs (Tor exit 185.220.101.1, Nmap scanme 45.33.32.156, Googlebot 66.249.66.1, Cloudflare 1.1.1.1)
  const DEMO_ATTACK = [
    '93.184.216.34 - - [14/Nov/2026:18:20:02 +0000] "GET / HTTP/1.1" 200 9420 "-" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"',
    '93.184.216.34 - - [14/Nov/2026:18:20:03 +0000] "GET /static/style.css HTTP/1.1" 200 4120 "https://stoprop.tech/" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"',
    '93.184.216.34 - - [14/Nov/2026:18:20:03 +0000] "GET /static/app.js HTTP/1.1" 200 6800 "https://stoprop.tech/" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"',
    '66.249.66.1 - - [14/Nov/2026:18:20:10 +0000] "GET /robots.txt HTTP/1.1" 200 180 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
    '66.249.66.1 - - [14/Nov/2026:18:20:18 +0000] "GET /methodology HTTP/1.1" 200 7210 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
    '185.220.101.1 - - [14/Nov/2026:18:20:25 +0000] "POST /wp-login.php HTTP/1.1" 401 512 "-" "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0"',
    '185.220.101.1 - - [14/Nov/2026:18:20:27 +0000] "POST /wp-login.php HTTP/1.1" 401 512 "-" "Mozilla/5.0 (Windows NT 10.0) Firefox/121.0"',
    '185.220.101.1 - - [14/Nov/2026:18:20:28 +0000] "POST /wp-login.php HTTP/1.1" 401 512 "-" "Mozilla/5.0 (Macintosh) Safari/605.1.15"',
    '185.220.101.1 - - [14/Nov/2026:18:20:30 +0000] "POST /wp-login.php HTTP/1.1" 401 512 "-" "Mozilla/5.0"',
    '185.220.101.1 - - [14/Nov/2026:18:20:31 +0000] "POST /xmlrpc.php HTTP/1.1" 403 310 "-" "Mozilla/5.0"',
    '185.220.101.1 - - [14/Nov/2026:18:20:33 +0000] "POST /wp-login.php HTTP/1.1" 429 240 "-" "Mozilla/5.0"',
    '45.33.32.156 - - [14/Nov/2026:18:20:40 +0000] "GET /.env HTTP/1.1" 404 162 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
    '45.33.32.156 - - [14/Nov/2026:18:20:41 +0000] "GET /.git/config HTTP/1.1" 404 162 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
    '45.33.32.156 - - [14/Nov/2026:18:20:42 +0000] "GET /phpmyadmin/ HTTP/1.1" 404 162 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
    '45.33.32.156 - - [14/Nov/2026:18:20:43 +0000] "GET /vendor/phpunit/eval-stdin.php HTTP/1.1" 403 190 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
    '159.89.12.44 - - [14/Nov/2026:18:20:50 +0000] "GET /article/1 HTTP/1.1" 200 6100 "-" "python-requests/2.32.3"',
    '159.89.12.44 - - [14/Nov/2026:18:20:51 +0000] "GET /article/2 HTTP/1.1" 200 5900 "-" "python-requests/2.32.3"',
    '159.89.12.44 - - [14/Nov/2026:18:20:52 +0000] "GET /article/3 HTTP/1.1" 200 6300 "-" "python-requests/2.32.3"',
    '159.89.12.44 - - [14/Nov/2026:18:20:53 +0000] "GET /article/4 HTTP/1.1" 200 6050 "-" "python-requests/2.32.3"',
  ].join("\n");
  const DEMO_CADDY = [
    '{"ts":1794680401,"request":{"client_ip":"93.184.216.34","method":"GET","uri":"/","headers":{"User-Agent":["Mozilla/5.0 Chrome/131.0"]}},"status":200,"size":8420}',
    '{"ts":1794680402,"request":{"client_ip":"93.184.216.34","method":"GET","uri":"/static/style.css","headers":{"User-Agent":["Mozilla/5.0 Chrome/131.0"]}},"status":200,"size":4120}',
    '{"ts":1794680408,"request":{"client_ip":"185.220.101.1","method":"POST","uri":"/api/login","headers":{"User-Agent":["Mozilla/5.0"]}},"status":401,"size":240}',
    '{"ts":1794680409,"request":{"client_ip":"185.220.101.1","method":"POST","uri":"/api/login","headers":{"User-Agent":["Mozilla/5.0"]}},"status":401,"size":240}',
    '{"ts":1794680410,"request":{"client_ip":"185.220.101.1","method":"POST","uri":"/api/login","headers":{"User-Agent":["Mozilla/5.0"]}},"status":401,"size":240}',
    '{"ts":1794680411,"request":{"client_ip":"185.220.101.1","method":"POST","uri":"/api/login","headers":{"User-Agent":["Mozilla/5.0"]}},"status":401,"size":240}',
    '{"ts":1794680412,"request":{"client_ip":"185.220.101.1","method":"POST","uri":"/api/login","headers":{"User-Agent":["Mozilla/5.0"]}},"status":429,"size":180}',
    '{"ts":1794680420,"request":{"client_ip":"45.33.32.156","method":"GET","uri":"/.env","headers":{"User-Agent":["curl/8.7.1"]}},"status":404,"size":150}',
    '{"ts":1794680421,"request":{"client_ip":"45.33.32.156","method":"GET","uri":"/.git/config","headers":{"User-Agent":["curl/8.7.1"]}},"status":404,"size":150}',
    '{"ts":1794680422,"request":{"client_ip":"45.33.32.156","method":"GET","uri":"/actuator/env","headers":{"User-Agent":["curl/8.7.1"]}},"status":404,"size":150}',
  ].join("\n");

  const DEMO_CLEAN = [
    '198.51.100.40 - - [14/Nov/2026:19:00:01 +0000] "GET / HTTP/1.1" 200 9120 "-" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"',
    '198.51.100.40 - - [14/Nov/2026:19:00:02 +0000] "GET /static/style.css HTTP/1.1" 200 4120 "https://stoprop.tech/" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"',
    '198.51.100.40 - - [14/Nov/2026:19:00:08 +0000] "GET /source HTTP/1.1" 200 7210 "https://stoprop.tech/" "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Safari/605.1.15"',
    '198.51.100.40 - - [14/Nov/2026:19:00:09 +0000] "GET /favicon.ico HTTP/1.1" 404 140 "https://stoprop.tech/" "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Safari/605.1.15"',
    '66.249.66.1 - - [14/Nov/2026:19:00:25 +0000] "GET /robots.txt HTTP/1.1" 200 180 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
    '66.249.66.1 - - [14/Nov/2026:19:00:40 +0000] "GET /metrics HTTP/1.1" 200 8300 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"',
  ].join("\n");

  function renderChart(tl) {
    const oc = $("traffic-chart");
    oc.replaceChildren();
    if (!tl || !tl.bins || !tl.bins.length) { oc.hidden = true; return; }
    oc.hidden = false;

    const head = el("div", "chart-head");
    head.append(el("span", "chart-title", "Request volume & bot traffic over time"));
    const leg = el("div", "chart-legend");
    [["Malicious (block)", "var(--red)"], ["Scraper (throttle)", "var(--amber)"], ["Real visitors & verified bots", "var(--green)"]].forEach(([lbl, c]) => {
      const it = el("span", "leg-item");
      const sw = el("span", "leg-swatch");
      sw.style.background = c;
      it.append(sw, document.createTextNode(lbl));
      leg.append(it);
    });
    head.append(leg);
    oc.append(head);

    const W = 740, H = 148, padL = 36, padR = 20, padT = 12, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const maxY = Math.max(3, ...tl.bins.map((b) => b.total));
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img", "aria-label": "Server request histogram" });
    [0, Math.round(maxY / 2), maxY].forEach((v) => {
      const y = padT + plotH - (v / maxY) * plotH;
      svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: y, y2: y, class: v === 0 ? "axis-line" : "grid-line" }));
      svg.append(svgEl("text", { x: padL - 6, y: y + 4, "text-anchor": "end", class: "ax-txt" }, v));
    });
    const bw = plotW / tl.bins.length, gap = Math.max(1.5, bw * 0.18);
    tl.bins.forEach((b, i) => {
      const x = padL + i * bw + gap / 2, w = Math.max(2, bw - gap);
      let curY = padT + plotH;
      [["clean", "var(--green)"], ["scraper", "var(--amber)"], ["malicious", "var(--red)"]].forEach(([k, fill]) => {
        if (!b[k]) return;
        const h = (b[k] / maxY) * plotH;
        curY -= h;
        svg.append(svgEl("rect", { x, y: curY, width: w, height: h, rx: 1.5, fill, "fill-opacity": k === "clean" ? "0.55" : "0.88" }));
      });
    });
    [0, 0.5, 1].forEach((f, idx) => {
      const x = padL + f * plotW;
      svg.append(svgEl("text", { x, y: H - 6, "text-anchor": idx === 0 ? "start" : idx === 2 ? "end" : "middle", class: "ax-txt" },
        fmtTick(tl.start + f * tl.span_s)));
    });
    oc.append(svg);
  }
  function renderActor(a) {
    const cat = CAT_LABEL[a.category] || { text: a.category, cls: "v-none", ico: "•" };
    const card = el("article", `card cluster ${cat.cls}`);
    const head = el("div", "cluster-head");
    head.append(
      el("span", `verdict ${cat.cls}`, `${cat.ico} ${cat.text}`),
      el("strong", null, a.ip),
      el("span", "pill", `${a.requests} req · ${a.rpm}/min`),
    );
    const itl = a.intel || {};
    if (itl.asn) head.append(el("span", "pill", `AS${itl.asn} ${itl.holder || ""}`.trim()));
    (itl.tags || []).forEach((t) => head.append(el("span", "pill warn", `Shodan: ${t}`)));
    if (itl.vulns_count) head.append(el("span", "pill warn", `${itl.vulns_count} CVEs`));
    if (itl.vt_malicious) head.append(el("span", "pill warn", `VirusTotal: ${itl.vt_malicious} malicious`));
    card.append(head);

    if (a.top_ua) card.append(el("p", "small muted", `User-Agent: ${a.top_ua}`));
    if (a.sample_paths && a.sample_paths.length) {
      card.append(el("p", "small", `Paths hit: ${a.sample_paths.join(", ")}`));
    }
    if (itl.ports && itl.ports.length) {
      card.append(el("p", "small muted", `Shodan open ports: ${itl.ports.join(", ")}${itl.hostnames && itl.hostnames.length ? ` · rDNS: ${itl.hostnames.join(", ")}` : ""}`));
    }
    if (a.reasons && a.reasons.length) {
      const ul = el("ul", "small");
      a.reasons.forEach((r) => ul.append(el("li", null, r)));
      card.append(ul);
    }
    if (a.innocent_notes && a.innocent_notes.length) {
      const ul = el("ul", "small muted");
      a.innocent_notes.forEach((r) => ul.append(el("li", null, r)));
      card.append(ul);
    }
    if (itl.links && itl.links.shodan) {
      const links = el("div", "platform-badges");
      links.append(el("span", "small muted", "External IP reputation:"));
      [["Shodan", itl.links.shodan], ["VirusTotal", itl.links.virustotal], ["AbuseIPDB", itl.links.abuseipdb]].forEach(([name, href]) => {
        if (!href) return;
        const link = el("a", "pill", `${name} ↗`);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer nofollow";
        links.append(link);
      });
      card.append(links);
    }
    return card;
  }

  function updateMitig() {
    if (!last) return;
    $("mitig-code").textContent = (last.mitigations && last.mitigations[activeFmt]) || "";
    document.querySelectorAll(".fmt-btn").forEach((b) => {
      const on = b.dataset.fmt === activeFmt;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
    });
  }
  function renderResult(r) {
    last = r;
    $("result").hidden = false;
    const s = r.summary;
    const kpis = $("traffic-kpis");
    kpis.replaceChildren();
    [
      ["Malicious actors (block)", `${s.malicious_ips} IP${s.malicious_ips === 1 ? "" : "s"}`, `${s.malicious_requests} attack requests caught`],
      ["Real visitors & polite bots", `${s.human_ips + s.verified_crawlers} IP${s.human_ips + s.verified_crawlers === 1 ? "" : "s"}`, `${s.clean_requests} clean requests protected`],
      ["Bulk / AI scrapers", `${s.scraper_ips} IP${s.scraper_ips === 1 ? "" : "s"}`, `${s.scraper_requests} scrape requests (rate-limit)`],
      ["Shodan + RIPE enriched", `${s.intel_enriched_ips} IP${s.intel_enriched_ips === 1 ? "" : "s"}`, `Format: ${s.format.toUpperCase()} (${s.requests} lines)`],
    ].forEach(([t, v, sub]) => {
      const c = el("div", "card kpi-card");
      c.append(el("div", "kpi-title muted small", t), el("div", "kpi-val", v), el("div", "muted small", sub));
      kpis.append(c);
    });

    $("summary").textContent = `Parsed ${s.requests} log entries across ${s.unique_ips} IPs in ${(r.elapsed_ms / 1000).toFixed(2)} s: ` +
      `${s.malicious_ips} malicious actor(s) recommended for immediate block, ${s.scraper_ips} bulk scraper(s) for rate-limiting, ` +
      `${s.verified_crawlers} verified crawler(s), and ${s.human_ips} real visitor IP(s) protected.`;
    renderChart(r.timeline);
    $("notes").replaceChildren(...(r.notes || []).map((n) => el("li", null, n)));
    updateMitig();
    $("actors-list").replaceChildren(...(r.actors || []).map(renderActor));
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function run(raw) {
    const text = (raw !== undefined ? raw : $("log-input").value).trim();
    if (!text) {
      $("status").className = "status error";
      $("status").textContent = "Please paste log lines, upload a file, or click a Try button above.";
      return;
    }
    $("go").disabled = true;
    $("status").className = "status";
    $("status").textContent = $("enrich").checked ? "Parsing logs & querying Shodan InternetDB + RIPE NCC ASN…" : "Analyzing logs…";
    try {
      const res = await fetch("/api/traffic", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: text, enrich: $("enrich").checked }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      $("status").textContent = "";
      renderResult(body);
    } catch (e) {
      $("status").className = "status error";
      $("status").textContent = e.message || "Could not analyze logs.";
    } finally {
      $("go").disabled = false;
    }
  }

  $("traffic-form").addEventListener("submit", (e) => { e.preventDefault(); run(); });
  $("ex-live-attack").addEventListener("click", () => { $("log-input").value = DEMO_ATTACK; run(DEMO_ATTACK); });
  $("ex-caddy").addEventListener("click", () => { $("log-input").value = DEMO_CADDY; run(DEMO_CADDY); });
  $("ex-office").addEventListener("click", () => { $("log-input").value = DEMO_CLEAN; run(DEMO_CLEAN); });
  document.querySelectorAll(".fmt-btn").forEach((b) => b.addEventListener("click", () => { activeFmt = b.dataset.fmt; updateMitig(); }));
  $("log-file").addEventListener("change", async () => {
    const f = $("log-file").files[0];
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) { $("file-info").textContent = "File exceeds 2 MB limit."; return; }
    const txt = await f.text();
    $("log-input").value = txt;
    $("file-info").textContent = `${f.name} loaded (${Math.round(f.size / 1024)} KB).`;
    run(txt);
  });
  $("copy-rules").addEventListener("click", async () => {
    if (!last) return;
    await navigator.clipboard.writeText($("mitig-code").textContent).catch(() => {});
    $("copy-rules").textContent = "✓ Copied";
    setTimeout(() => { $("copy-rules").textContent = "Copy rules"; }, 1600);
  });
  $("copy-traffic-brief").addEventListener("click", async () => {
    if (!last) return;
    const lines = ["=== Stoprop Clean-Traffic Security Brief ===", $("summary").textContent];
    (last.actors || []).filter((a) => a.severity === "malicious" || a.severity === "scraper").forEach((a) => {
      lines.push(`• ${a.ip} [${a.category}] (${a.requests} req): ${(a.reasons || []).join(" ")}`);
    });
    lines.push("", "Analyzed via https://stoprop.tech/traffic");
    await navigator.clipboard.writeText(lines.join("\n")).catch(() => {});
    $("copy-traffic-brief").textContent = "✓ Copied brief";
    setTimeout(() => { $("copy-traffic-brief").textContent = "Copy brief"; }, 1600);
  });
})();
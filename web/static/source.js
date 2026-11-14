// /source page: receipts only (no LLM cost).
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let lastTarget = null;

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
    status.textContent = refresh ? "Checking again…" : "Checking… (some sources take a few seconds)";
    try {
      const body = { refresh: !!refresh };
      if (/^https?:\/\//i.test(target)) body.url = target; else body.domain = target;
      const r = await fetch("/api/receipts", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Something went wrong (HTTP ${r.status}).`);
      lastTarget = target;
      $("domain").textContent = data.domain;
      window.StopropReceipts.render($("receipts"), data.receipts);
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

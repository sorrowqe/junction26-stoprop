// Shared receipts renderer (analyzer page + /source). Vanilla JS, no build step.
window.StopropReceipts = (() => {
  "use strict";

  const LOOK = {
    ok:          { icon: "✓", label: "Reassuring" },
    warning:     { icon: "!", label: "Worth a closer look" },
    neutral:     { icon: "i", label: "Information" },
    not_checked: { icon: "–", label: "Not checked" },
    error:       { icon: "×", label: "Check failed" },
  };

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  function ago(iso) {
    if (!iso) return "not checked";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "checked just now";
    if (s < 3600) return `checked ${Math.round(s / 60)} min ago`;
    if (s < 86400) return `checked ${Math.round(s / 3600)} h ago`;
    return `checked ${Math.round(s / 86400)} days ago`;
  }

  function look(r) {
    if (r.status === "not_checked") return "not_checked";
    if (r.status === "error") return "error";
    return LOOK[r.flag] ? r.flag : "neutral";
  }

  function render(list, receipts) {
    list.textContent = "";
    receipts.forEach((r) => {
      const kind = look(r);
      const li = el("li", `receipt r-${kind}`);
      const ico = el("span", "r-ico", LOOK[kind].icon);
      ico.setAttribute("aria-hidden", "true");
      const body = el("div", "r-body");
      const head = el("div", "r-head");
      head.append(el("strong", null, r.title), el("span", "r-kind", LOOK[kind].label));
      body.append(head, el("p", "r-text", r.text));
      if (r.details && Array.isArray(r.details.items) && r.details.items.length > 0) {
        const sub = el("ul", "r-sublist small");
        r.details.items.slice(0, 5).forEach((it) => {
          const sli = el("li", null, null);
          sli.append(el("strong", null, `${it.publisher || "Report"}: `));
          if (it.url && /^https?:\/\//.test(it.url)) {
            const a = el("a", null, it.claim || it.url);
            a.href = it.url; a.target = "_blank"; a.rel = "noopener noreferrer nofollow";
            sli.append(a);
          } else {
            sli.append(document.createTextNode(it.claim || ""));
          }
          if (it.rating) sli.append(document.createTextNode(` (${it.rating})`));
          sub.append(sli);
        });
        body.append(sub);
      }
      if (r.details && Array.isArray(r.details.domains) && r.details.domains.length > 0) {
        const pills = el("div", "chart-meta-pills");
        r.details.domains.slice(0, 8).forEach((d) => pills.append(el("span", "pill", d)));
        body.append(pills);
      }
      const meta = el("p", "r-meta");
      const when = el("span", "r-when", ago(r.checked_at));
      if (r.checked_at) when.dataset.checked = r.checked_at;
      meta.append(when);
      if (r.refreshing) meta.append(" · refreshing in the background");
      if (r.evidence_url) {
        const a = el("a", null, "evidence");
        a.href = r.evidence_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        meta.append(" · ", a);
      }
      body.append(meta);
      if (r.note) body.append(el("p", "r-note", r.note));
      li.append(ico, body);
      list.append(li);
    });
  }

  // keep "checked X ago" current
  setInterval(() => {
    document.querySelectorAll(".r-when[data-checked]").forEach((n) => { n.textContent = ago(n.dataset.checked); });
  }, 30000);

  async function refresh(target, title) {
    const body = { refresh: true, title: title || null };
    if (/^https?:\/\//i.test(target)) body.url = target; else body.domain = target;
    const r = await fetch("/api/receipts", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${r.status}`);
    return data;
  }

  return { render, refresh, ago };
})();

// Shared helpers: settings, the channel between the chat page and the dashboard, and a tiny markdown renderer.
(function () {
  const CFG_KEY = "gov-demo-config-v1";
  const MODE_KEY = "gov-demo-mode-v1";
  const defaults = window.DEMO_CONFIG || {};

  function loadConfig() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(CFG_KEY) || "{}"); } catch (e) { /* ignore */ }
    const m = (a, b) => Object.assign({}, a, b || {});
    return {
      audit_url: saved.audit_url || defaults.audit_url || "http://localhost:8090",
      governed: m(defaults.governed, saved.governed),
      ungoverned: m(defaults.ungoverned, saved.ungoverned),
      user: m(defaults.user, saved.user),
    };
  }
  function saveConfig(cfg) { try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); } catch (e) { /* ignore */ } }
  function loadMode() { try { return localStorage.getItem(MODE_KEY) === "on"; } catch (e) { return false; } }
  function saveMode(on) { try { localStorage.setItem(MODE_KEY, on ? "on" : "off"); } catch (e) { /* ignore */ } }

  function chatEndpoint(url) {
    const u = (url || "").trim();
    if (!u) return "";
    return /\/chat\/?$/.test(u) ? u : u.replace(/\/+$/, "") + "/chat";
  }

  const channel = "BroadcastChannel" in window ? new BroadcastChannel("gov-demo") : null;
  function emit(msg) { if (channel) channel.postMessage(msg); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function resetDemo(cfg) {
    try { await fetch(cfg.audit_url.replace(/\/+$/, "") + "/admin/reset", { method: "POST" }); } catch (e) { /* audit server may be offline */ }
    emit({ kind: "reset" });
  }

  // ---- markdown: escape first, then format. Supports headings, lists, tables, code, bold.
  function inline(s) {
    return s.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }
  function md(src) {
    const lines = esc(src).split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (/^```/.test(line)) {
        const buf = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
        i++; out.push("<pre>" + buf.join("\n") + "</pre>"); continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line)) {
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
        const body = rows.filter((r) => !/^[\s|:-]+$/.test(r)); // drop the |---|---| separator row
        if (body.length) {
          const head = cells(body[0]).map((c) => `<th>${c}</th>`).join("");
          const rest = body.slice(1).map((r) => "<tr>" + cells(r).map((c) => `<td>${c}</td>`).join("") + "</tr>").join("");
          out.push(`<table><thead><tr>${head}</tr></thead><tbody>${rest}</tbody></table>`);
        }
        continue;
      }
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) { out.push(`<h${h[1].length < 3 ? 3 : 4}>${inline(h[2])}</h${h[1].length < 3 ? 3 : 4}>`); i++; continue; }
      if (/^\s*([-*•])\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*([-*•])\s+/.test(lines[i])) items.push("<li>" + inline(lines[i++].replace(/^\s*([-*•])\s+/, "")) + "</li>");
        out.push("<ul>" + items.join("") + "</ul>"); continue;
      }
      if (/^\s*\d+[.)]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push("<li>" + inline(lines[i++].replace(/^\s*\d+[.)]\s+/, "")) + "</li>");
        out.push("<ol>" + items.join("") + "</ol>"); continue;
      }
      if (!line.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*•]|\d+[.)])\s+|\s*\|)/.test(lines[i])) para.push(inline(lines[i++]));
      out.push("<p>" + para.join("<br>") + "</p>");
    }
    return out.join("");
  }

  window.Gov = { loadConfig, saveConfig, loadMode, saveMode, chatEndpoint, channel, emit, esc, md, resetDemo };
})();

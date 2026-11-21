/* domain-cert-monitor UI: plain JS, no build step, no dependencies.
 *
 * Routes (hash): #/  dashboard · #/domains/<id>  detail · #/settings
 * Data comes from the JSON API (/api/meta, /api/domains, ...). All text from the
 * API is inserted with textContent; the only innerHTML is the static icon SVG below.
 */
"use strict";

// --- icons (static markup) ---------------------------------------------------

const ICON = {
  ok: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg>',
  warn: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l10 18H2L12 3z"/><path d="M12 10v4M12 17.5v.5"/></svg>',
  crit: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></svg>',
  unk: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17v.5"/></svg>',
  refresh: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 0 0-14.9-3M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4"/></svg>',
  plus: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  sun: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  back: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>',
  lock: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  mail: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
};
const STATUS_ICON = { green: ICON.ok, yellow: ICON.warn, red: ICON.crit, grey: ICON.unk };
const RANK = { red: 0, yellow: 1, green: 2, grey: 3 };

const POLL_ACTIVE_MS = 1500;
const POLL_IDLE_MS = 30000;

// --- DOM helper -----------------------------------------------------------------

/** h("div.a.b", {attr: v, onclick: fn}, child, "text", [children]) */
function h(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  if (attrs == null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs); // no attributes given; this is the first child (may be 0)
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // static icons only
    else if (k === "class") el.className += (el.className ? " " : "") + v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}
function icon(svg) {
  const span = document.createElement("span");
  span.innerHTML = svg;
  return span.firstChild;
}

// --- API -------------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function api(method, path, body) {
  const opts = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError(0, "network", "Can't reach the server.");
  }
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    /* not JSON */
  }
  if (!res.ok) {
    const d = (data && data.detail) || {};
    throw new ApiError(res.status, d.code || "http_" + res.status, d.message || res.statusText);
  }
  return data;
}

// --- formatting ---------------------------------------------------------------------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
let displayTz = "UTC";
const partsCache = new Map();

function parts(iso, tz) {
  const key = tz;
  let fmt = partsCache.get(key);
  if (!fmt) {
    try {
      fmt = new Intl.DateTimeFormat("en-GB", {
        timeZone: tz, year: "numeric", month: "numeric", day: "numeric",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      });
    } catch (e) {
      fmt = new Intl.DateTimeFormat("en-GB", {
        timeZone: "UTC", year: "numeric", month: "numeric", day: "numeric",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      });
    }
    partsCache.set(key, fmt);
  }
  const out = {};
  for (const p of fmt.formatToParts(new Date(iso))) out[p.type] = p.value;
  return out;
}
/** "12 Apr 2015" in the display time zone */
function fmtDate(iso, tz = displayTz) {
  if (!iso) return "—";
  const p = parts(iso, tz);
  return `${+p.day} ${MONTHS[p.month - 1]} ${p.year}`;
}
/** "21 Nov 2026, 15:12" */
function fmtDateTime(iso, tz = displayTz) {
  if (!iso) return "—";
  const p = parts(iso, tz);
  return `${fmtDate(iso, tz)}, ${p.hour}:${p.minute}`;
}
/** Exact UTC, for hover titles. */
function fmtUtc(iso) {
  return iso ? fmtDateTime(iso, "UTC") + " UTC" : "";
}
function tzSuffix() {
  return displayTz === "UTC" ? " UTC" : "";
}

function plural(n, word) {
  return `${n} ${word}${Math.abs(n) === 1 ? "" : "s"}`;
}
/** "14 min ago", "3 h ago", "2 days ago" */
function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${plural(Math.floor(s / 86400), "day")} ago`;
}
/** "5 h 46 min", "12 min" */
function until(iso) {
  const m = Math.max(0, Math.round((Date.parse(iso) - Date.now()) / 60000));
  if (m < 1) return "less than a minute";
  if (m < 60) return `${m} min`;
  const hrs = Math.floor(m / 60);
  return m % 60 ? `${hrs} h ${m % 60} min` : `${hrs} h`;
}
/** Relative text for a days_left value. */
function daysText(days) {
  if (days == null) return "";
  if (days < 0) return `expired ${plural(-days, "day")} ago`;
  if (days === 0) return "expires today";
  return `in ${plural(days, "day")}`;
}
function fmtDays(days) {
  return days < 0 ? "−" + -days : String(days);
}
function intervalText(hours) {
  if (hours === 1) return "Every hour";
  if (hours * 60 < 1) return `Every ${Math.round(hours * 3600)} seconds`;
  if (hours < 1) return `Every ${+(hours * 60).toFixed(1)} minutes`;
  return `Every ${+hours.toFixed(2)} hours`;
}

// --- status helpers --------------------------------------------------------------------

/** Label for a domain's overall status, taken from the kind that caused it. */
function overallLabel(d) {
  if (d.overall_status === "grey") return "Unknown";
  for (const k of [d.cert, d.domain]) if (k.status === d.overall_status) return k.label;
  return "Unknown";
}
function pill(status, label) {
  return h("span.pill." + status, icon(STATUS_ICON[status]), label);
}
function dotLabel(status, label) {
  return h("span.dot-label." + status, h("span.dot", { "aria-hidden": "true" }), label);
}
/** The kind with the fewest days left among known expiries. */
function soonest(d) {
  const known = [["certificate", d.cert], ["domain", d.domain]].filter(([, k]) => k.days_left != null);
  if (!known.length) return null;
  known.sort((a, b) => a[1].days_left - b[1].days_left);
  return { name: known[0][0], days: known[0][1].days_left, only: known.length === 1 };
}

// --- state, polling, toast ---------------------------------------------------------------

const state = { meta: null, domains: [], error: null };
let pollTimer = null;
let toastTimer = null;

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 4000);
}

function busy() {
  return (state.meta && state.meta.run_in_progress) || state.domains.some((d) => d.checking);
}

async function refresh() {
  clearTimeout(pollTimer);
  try {
    const [meta, domains] = await Promise.all([api("GET", "api/meta"), api("GET", "api/domains")]);
    state.meta = meta;
    state.domains = domains;
    state.error = null;
    displayTz = meta.display_timezone || "UTC";
  } catch (e) {
    state.error = e.message;
  }
  await render();
  pollTimer = setTimeout(refresh, busy() ? POLL_ACTIVE_MS : POLL_IDLE_MS);
}

// --- actions ----------------------------------------------------------------------------------

async function checkOne(d, button) {
  if (button) button.disabled = true;
  try {
    const r = await api("POST", `api/domains/${d.id}/check`);
    if (!r.started) toast(`${d.hostname} is already being checked.`);
  } catch (e) {
    toast(e.message);
  }
  refresh();
}

async function checkAll() {
  try {
    const r = await api("POST", "api/checks/run");
    if (!r.started) toast("A full check is already running.");
  } catch (e) {
    toast(e.message);
  }
  refresh();
}

async function removeDomain(d) {
  if (!confirm(`Remove ${d.hostname}? Its check results and alert records are deleted too.`)) return;
  try {
    await api("DELETE", `api/domains/${d.id}`);
    toast(`Removed ${d.hostname}.`);
  } catch (e) {
    if (e.status !== 404) {
      toast(e.message);
      return;
    }
  }
  if (location.hash.startsWith("#/domains/")) location.hash = "#/";
  refresh();
}

// --- add dialog ------------------------------------------------------------------------------

function setupAddDialog() {
  const dialog = document.getElementById("add-dialog");
  const form = document.getElementById("add-form");
  const input = document.getElementById("add-hostname");
  const err = document.getElementById("add-error");
  const submit = document.getElementById("add-submit");

  const clearError = () => {
    err.textContent = "";
    input.removeAttribute("aria-invalid");
  };
  document.getElementById("add-open").addEventListener("click", () => {
    form.reset();
    clearError();
    dialog.showModal();
    input.focus();
  });
  document.getElementById("add-cancel").addEventListener("click", () => dialog.close());
  input.addEventListener("input", clearError);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    clearError();
    if (!input.value.trim()) {
      err.textContent = "Please enter a hostname, e.g. example.com.";
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return;
    }
    submit.disabled = true;
    try {
      const d = await api("POST", "api/domains", { hostname: input.value });
      dialog.close();
      toast(`Added ${d.hostname}. Checking now…`);
      if (location.hash !== "#/" && location.hash !== "") location.hash = "#/";
      refresh();
    } catch (e) {
      err.textContent = e.message;
      input.setAttribute("aria-invalid", "true");
      input.focus();
    } finally {
      submit.disabled = false;
    }
  });
}

// --- header -------------------------------------------------------------------------------------

function renderHeader(route) {
  for (const a of document.querySelectorAll("[data-nav]")) {
    if (a.dataset.nav === route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  const dark = document.documentElement.dataset.theme === "dark";
  const toggle = document.getElementById("theme-toggle");
  toggle.replaceChildren(icon(dark ? ICON.sun : ICON.moon));
  toggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");

  const running = state.meta && state.meta.run_in_progress;
  const checkAllBtn = document.getElementById("check-all");
  checkAllBtn.replaceChildren(icon(ICON.refresh), running ? "Checking…" : "Check all");
  checkAllBtn.disabled = !!running;
  checkAllBtn.classList.toggle("spin", !!running);
}

function setupHeader() {
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("dcm-theme", next);
    } catch (e) {
      /* storage unavailable */
    }
    renderHeader(currentRoute().name);
  });
  document.getElementById("check-all").addEventListener("click", checkAll);
  document.getElementById("add-open").replaceChildren(icon(ICON.plus), "Add domain");
}

function banners() {
  const out = [];
  if (state.error) {
    out.push(h("div.banner.error", { role: "alert" }, h("p", state.error, " Retrying…")));
  }
  if (state.meta && !state.meta.smtp_configured) {
    out.push(
      h("div.banner.warn", { role: "note" }, icon(ICON.mail),
        h("p", h("strong", "Email alerts are off."), " ",
          h("span.muted", "SMTP is not configured. Set ",
            h("span.mono", "SMTP_HOST"), ", ", h("span.mono", "SMTP_FROM"), " and ",
            h("span.mono", "ALERT_TO"), " in ", h("span.mono", ".env"),
            " and restart the container."))));
  }
  return out;
}

// --- dashboard --------------------------------------------------------------------------------------

function renderDashboard() {
  const { meta, domains } = state;
  const counts = { red: 0, yellow: 0, grey: 0, green: 0 };
  for (const d of domains) counts[d.overall_status]++;

  const sub = [plural(domains.length, "domain")];
  if (meta) {
    sub.push(meta.last_run_at ? `last full check ${fmtDateTime(meta.last_run_at)}${tzSuffix()}` : "no full check yet");
    if (meta.run_in_progress) sub.push("full check running");
    else if (meta.next_run_at) sub.push(`next in ${until(meta.next_run_at)}`);
  }

  let next = null;
  for (const d of domains) {
    for (const [name, k] of [["certificate", d.cert], ["domain", d.domain]]) {
      if (k.days_left != null && k.days_left >= 0 && (!next || k.days_left < next.days)) {
        next = { host: d.hostname, name, days: k.days_left };
      }
    }
  }

  const tile = (status, title, note) =>
    h("div.tile." + status + (counts[status] && (status === "red" || status === "yellow") ? ".active" : ""),
      h("div.tile-title", icon(STATUS_ICON[status]), title),
      h("div.tile-count" + (counts[status] ? "" : ".zero"), counts[status]),
      h("div.tile-note", note));

  return h("main", ...banners(),
    h("div.page-head",
      h("div", h("h1", "Overview"), h("p.muted", sub.join(" · "))),
      next && h("p.muted", { style: "margin:0" }, "Next to expire: ",
        h("span.mono", { style: "color:var(--text)" }, next.host), ` ${next.name} `,
        h("strong", { style: "color:var(--text)" }, next.days === 0 ? "today" : `in ${plural(next.days, "day")}`))),
    h("section.summary", { "aria-label": "Status summary" },
      tile("red", "Critical", "7 days or less, expired, or check failed"),
      tile("yellow", "Expiring soon", "30 days or less"),
      tile("grey", "Unknown", "Not checked yet"),
      tile("green", "OK", "More than 30 days left")),
    h("section.card", { "aria-labelledby": "domains-h" },
      h("div.card-head", h("h2", { id: "domains-h" }, "Domains"),
        h("span.muted", { style: "font-size:13px" }, "Sorted by urgency")),
      domains.length ? domainTable(domains) : emptyState()));
}

function emptyState() {
  return h("div.empty",
    h("p", "No domains yet. Add one to start watching its certificate and registration."),
    h("button.btn.btn-primary", { type: "button", onclick: () => document.getElementById("add-open").click() },
      icon(ICON.plus), "Add domain"));
}

function domainTable(domains) {
  return h("div.table-wrap",
    h("table.domains",
      h("thead", h("tr",
        h("th", { scope: "col" }, "Hostname"),
        h("th", { scope: "col" }, "Certificate expires"),
        h("th", { scope: "col" }, "Domain expires"),
        h("th", { scope: "col" }, "Days left"),
        h("th", { scope: "col" }, "Status"),
        h("th", { scope: "col" }, "Last check"),
        h("th", { scope: "col" }, h("span.sr-only", "Actions")))),
      h("tbody", domains.map(domainRow))));
}

function certCell(d) {
  const c = d.cert;
  if (!c.checked_at) return [h("div.muted", d.checking ? "Checking…" : "Not checked yet")];
  if (!c.expires_at) {
    return [h("div.fg.red", "No certificate"), h("div.note.fg.red", c.error || "Check failed")];
  }
  let note;
  if (c.days_left < 0) note = h("div.note.fg.red", c.verify_error && c.verify_error !== "certificate expired" ? `Expired · ${c.verify_error}` : "Expired");
  else if (c.verified === false) note = h("div.note.fg.red", c.verify_error || "Verification failed");
  else note = h("div.note", daysText(c.days_left));
  return [h("div", { title: fmtUtc(c.expires_at) }, fmtDate(c.expires_at)), note];
}

function domainCell(d) {
  const k = d.domain;
  if (!k.checked_at) return [h("div.muted", d.checking ? "Checking…" : "Not checked yet")];
  if (!k.expires_at) {
    if (k.error) return [h("div.fg.red", "Lookup failed"), h("div.note.fg.red", k.error)];
    return [h("div.fg.grey", "Unknown"), h("div.note", "not published via RDAP")];
  }
  const note = k.error
    ? h("div.note.fg.yellow", `last lookup failed: ${k.error}`)
    : h("div.note", daysText(k.days_left));
  return [h("div", { title: fmtUtc(k.expires_at) }, fmtDate(k.expires_at)), note];
}

function daysCell(d) {
  const s = soonest(d);
  if (!s) return [h("div.muted", "—")];
  const st = s.days <= 7 ? "red" : s.days <= 30 ? "yellow" : "";
  return [
    h("div.days" + (st ? ".fg." + st : ""), fmtDays(s.days)),
    h("div.note", s.only && s.name === "certificate" && d.domain.checked_at ? "certificate only" : s.name),
  ];
}

function domainRow(d) {
  const checkBtn = h("button.btn.btn-sm" + (d.checking ? ".spin" : ""), {
    type: "button", "aria-label": `Check ${d.hostname} now`, disabled: d.checking,
    onclick: (ev) => checkOne(d, ev.currentTarget),
  }, icon(ICON.refresh), d.checking ? "Checking…" : "Check now");
  const removeBtn = h("button.btn.btn-sm.btn-danger", {
    type: "button", "aria-label": `Remove ${d.hostname}`, onclick: () => removeDomain(d),
  }, "Remove");

  return h("tr",
    h("td", h("a.mono.host", { href: `#/domains/${d.id}` }, d.hostname)),
    h("td", certCell(d)),
    h("td", domainCell(d)),
    h("td", daysCell(d)),
    h("td", pill(d.overall_status, overallLabel(d))),
    h("td", d.last_checked_at
      ? [h("div", { title: fmtUtc(d.last_checked_at) }, ago(d.last_checked_at)),
         h("div.note", { title: fmtUtc(d.last_checked_at) }, fmtDateTime(d.last_checked_at).replace(/ \d{4},/, ","))]
      : h("div.muted", d.checking ? "Checking…" : "Never")),
    h("td.actions-cell", checkBtn, removeBtn));
}

// --- detail ----------------------------------------------------------------------------------------

async function renderDetail(id) {
  let d = state.domains.find((x) => String(x.id) === id);
  if (!d) {
    try {
      d = await api("GET", `api/domains/${encodeURIComponent(id)}`);
    } catch (e) {
      return h("main", ...banners(), backLink(),
        h("h1", e.status === 404 ? "Domain not found" : "Couldn't load domain"),
        h("p.muted", e.status === 404 ? "It may have been removed." : e.message));
    }
  }
  document.title = `${d.hostname} — domain-cert-monitor`;

  const checkBtn = h("button.btn" + (d.checking ? ".spin" : ""), {
    type: "button", disabled: d.checking, onclick: (ev) => checkOne(d, ev.currentTarget),
  }, icon(ICON.refresh), d.checking ? "Checking…" : "Check now");

  const metaLine = [`Port 443 · added ${fmtDate(d.created_at)}`];
  if (d.last_checked_at) metaLine.push(`last check ${fmtDateTime(d.last_checked_at)}${tzSuffix()} (${ago(d.last_checked_at)})`);
  else metaLine.push(d.checking ? "checking now" : "not checked yet");

  return h("main", ...banners(), backLink(),
    h("div.detail-head",
      h("div",
        h("div.title-row", h("h1.mono", d.hostname), pill(d.overall_status, overallLabel(d))),
        h("p.muted", metaLine.join(" · "))),
      h("div.actions", checkBtn,
        h("button.btn.btn-danger", { type: "button", onclick: () => removeDomain(d) }, "Remove"))),
    h("div.panels", certPanel(d), domainPanel(d)));
}

function backLink() {
  return h("a.back", { href: "#/" }, icon(ICON.back), "All domains");
}

function bigDays(k, unknownText) {
  if (k.days_left == null) {
    return h("div.big", h("strong.fg.grey", "—"), h("span.muted", unknownText));
  }
  const st = k.days_left <= 7 ? "red" : k.days_left <= 30 ? "yellow" : "";
  return h("div.big",
    h("strong" + (st ? ".fg." + st : ""), fmtDays(k.days_left)),
    h("span.muted", k.days_left < 0 ? `days · ${daysText(k.days_left)}` : k.days_left === 1 ? "day left" : "days left"));
}

function panel(id, title, k, ...body) {
  return h("section.card.panel", { "aria-labelledby": id },
    h("div.panel-head", h("h2", { id }, title), dotLabel(k.status, k.label)),
    ...body);
}

function certPanel(d) {
  const c = d.cert;
  const det = c.details || {};
  if (!c.checked_at) {
    return panel("cert-h", "TLS certificate", c, h("p.muted", { style: "margin:0" }, d.checking ? "Checking…" : "Not checked yet."));
  }
  const facts = [];
  const fact = (k, v, cls) => facts.push(h("dt", k), h("dd" + (cls || ""), v));
  if (det.subject) fact("Subject", det.subject, ".mono");
  if (det.issuer) fact("Issuer", det.issuer);
  if (det.sans && det.sans.length) fact("Names (SAN)", det.sans.join(", "), ".mono");
  if (c.expires_at) {
    fact("Valid until", h("span", { title: fmtUtc(c.expires_at) }, `${fmtDateTime(c.expires_at)}${tzSuffix()}`),
      c.status === "red" ? ".fg.red" : "");
  }
  if (c.verified != null) {
    fact("Verified", c.verified ? "Yes: chain and hostname are valid" : `No: ${c.verify_error || "verification failed"}`,
      c.verified ? "" : ".fg.red");
  }
  if (det.ip) fact("Address", det.ip, ".mono");
  fact("Checked", h("span", { title: fmtUtc(c.checked_at) }, `${fmtDateTime(c.checked_at)}${tzSuffix()} (${ago(c.checked_at)})`));

  return panel("cert-h", "TLS certificate", c,
    c.error ? h("div.err-box", c.error) : bigDays(c, ""),
    h("dl.facts", facts));
}

function domainPanel(d) {
  const k = d.domain;
  const det = k.details || {};
  if (!k.checked_at) {
    return panel("dom-h", "Domain registration", k, h("p.muted", { style: "margin:0" }, d.checking ? "Checking…" : "Not checked yet."));
  }
  const facts = [];
  const fact = (key, v, cls) => facts.push(h("dt", key), h("dd" + (cls || ""), v));
  if (det.registrable_domain) fact("Domain", det.registrable_domain, ".mono");
  fact("Expires", k.expires_at ? h("span", { title: fmtUtc(k.expires_at) }, fmtDate(k.expires_at)) : "Unknown");
  if (det.rdap_server) {
    const url = String(det.rdap_server);
    fact("RDAP source", /^https?:\/\//.test(url)
      ? h("a.mono", { href: url, rel: "noopener noreferrer", target: "_blank", style: "font-size:13px" }, url.replace(/^https?:\/\//, ""))
      : url);
  }
  fact("Looked up", h("span", { title: fmtUtc(k.checked_at) }, `${fmtDateTime(k.checked_at)}${tzSuffix()} (${ago(k.checked_at)})`));

  const top = [];
  if (k.error && !k.expires_at) top.push(h("div.err-box", k.error));
  else {
    top.push(bigDays(k, "expiry date not published via RDAP"));
    if (k.error) top.push(h("div.err-box", `Last lookup failed: ${k.error}. Showing the last known date.`));
  }
  if (!k.expires_at && !k.error) {
    top.push(h("p.muted", { style: "margin:0" },
      "The registry doesn't publish an expiry date via RDAP. The overall status comes from the certificate."));
  }
  return panel("dom-h", "Domain registration", k, ...top, h("dl.facts", facts));
}

// --- settings ---------------------------------------------------------------------------------------

function renderSettings() {
  const m = state.meta;
  if (!m) return h("main.narrow", ...banners(), h("h1", "Settings"));
  const s = m.smtp;
  const row = (key, value, variable, mono) =>
    h("div.setting", h("span.k", key), h("span.v" + (mono ? ".mono" : ""), value),
      variable && h("span.var.mono", variable));
  const notSet = () => h("span.muted", "Not set");
  const thresholdsDesc = [...m.alert_thresholds].sort((a, b) => b - a);
  const security = { starttls: "STARTTLS", tls: "TLS", none: "None (unencrypted)" }[s.security] || s.security;

  return h("main.narrow", ...banners(),
    h("div", h("h1", "Settings")),
    h("div.banner", { role: "note" }, icon(ICON.lock),
      h("p", h("strong", "Read-only."), " ",
        h("span.muted", "These values come from ", h("span.mono", { style: "color:var(--text)" }, ".env"),
          " on the server. Edit the file and restart the container to change them."))),

    h("section.card", { "aria-labelledby": "checks-h" },
      h("h2", { id: "checks-h" }, "Checks"),
      h("div.setting", h("span.k", "Check interval"),
        h("span.v", { style: "font-weight:600" }, intervalText(m.check_interval_hours)),
        h("span.var.mono", `CHECK_INTERVAL_HOURS=${m.check_interval_hours}`)),
      row("Last full run", m.last_run_at
        ? `${fmtDateTime(m.last_run_at)}${tzSuffix()} · ${plural(state.domains.length, "domain")}`
        : "None since the app started"),
      row("Next run", m.run_in_progress ? "Running now"
        : m.next_run_at ? `${fmtDateTime(m.next_run_at)}${tzSuffix()} (in ${until(m.next_run_at)})` : "Not scheduled")),

    h("section.card", { "aria-labelledby": "thr-h" },
      h("h2", { id: "thr-h" }, "Alert thresholds"),
      h("div.setting", { style: "align-items:center" }, h("span.k", "Send an email at"),
        h("div.chips", thresholdsDesc.map((t) => h("span.chip", plural(t, "day")))),
        h("span.var.mono", `ALERT_THRESHOLDS=${thresholdsDesc.join(",")}`)),
      h("div.legend",
        h("div.green", icon(ICON.ok), "OK · over 30 days"),
        h("div.yellow", icon(ICON.warn), "Soon · 30 days or less"),
        h("div.red", icon(ICON.crit), "Critical · 7 days or less"),
        h("div.grey", icon(ICON.unk), "Unknown · no data"))),

    h("section.card", { "aria-labelledby": "smtp-h" },
      h("div.card-head", { style: "padding:12px 24px;border-bottom:1px solid var(--border)" },
        h("div", { style: "display:flex;flex-wrap:wrap;align-items:center;gap:12px" },
          h("h2", { id: "smtp-h" }, "Email (SMTP)"),
          m.smtp_configured ? pill("green", "Configured") : pill("grey", "Not configured"))),
      row("Server", s.host ? `${s.host}:${s.port}` : notSet(), "SMTP_HOST · SMTP_PORT", !!s.host),
      row("Encryption", security, "SMTP_SECURITY"),
      row("Username", s.user || notSet(), "SMTP_USER", !!s.user),
      row("Password", s.password_set ? "Set (hidden)" : notSet(), "SMTP_PASSWORD"),
      row("From", s.from_address || notSet(), "SMTP_FROM", !!s.from_address),
      row("Send alerts to", s.recipients.length ? s.recipients.join(", ") : notSet(), "ALERT_TO", s.recipients.length > 0)),

    h("section.card", { "aria-labelledby": "gen-h" },
      h("h2", { id: "gen-h" }, "General"),
      row("Link in emails", m.base_url, "BASE_URL", true),
      row("Time zone", m.display_timezone, "TZ", true),
      row("Version", m.version, null, true)));
}

// --- routing -----------------------------------------------------------------------------------------

function currentRoute() {
  const hash = location.hash || "#/";
  let m;
  if ((m = hash.match(/^#\/domains\/(\d+)$/))) return { name: "dashboard", id: m[1] };
  if (hash === "#/settings") return { name: "settings" };
  return { name: "dashboard" };
}

let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;
  const route = currentRoute();
  renderHeader(route.name);
  let view;
  if (route.id) view = await renderDetail(route.id);
  else if (route.name === "settings") view = renderSettings();
  else view = renderDashboard();
  if (seq !== renderSeq) return; // a newer render started meanwhile
  if (!route.id) document.title = route.name === "settings" ? "Settings — domain-cert-monitor" : "Dashboard — domain-cert-monitor";

  const app = document.getElementById("app");
  // Keep keyboard focus on the same control across re-renders where possible.
  const active = document.activeElement;
  const focusKey = active && app.contains(active) ? active.getAttribute("aria-label") || active.textContent : null;
  app.replaceChildren(view);
  if (focusKey) {
    const again = [...app.querySelectorAll("button, a")].find(
      (el) => (el.getAttribute("aria-label") || el.textContent) === focusKey);
    if (again) again.focus();
  }
}

window.addEventListener("hashchange", () => {
  window.scrollTo(0, 0);
  render();
});

setupHeader();
setupAddDialog();
refresh();

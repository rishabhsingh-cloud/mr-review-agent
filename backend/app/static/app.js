/* ============================================================
   MR Review Agent — Dashboard app (vanilla JS, no deps)
   Talks to the same-origin FastAPI endpoints from Bit 10.
   ============================================================ */

"use strict";

/* ---- tiny helpers ---------------------------------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
};

const api = async (path, opts) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
};

const fmtUSD = (n) => "$" + Number(n || 0).toFixed(n >= 1 ? 2 : 4);
const fmtNum = (n) => (n == null ? "—" : Number(n).toLocaleString());
const fmtTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
const clsSafe = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

const badge = (text, kind, prefix = "") =>
  el("span", { class: `badge ${prefix}${clsSafe(text)}` }, text || "—");
const sevBadge = (s) => badge(s || "info", "sev", "sev-");
const statusBadge = (s) => badge(s || "—", "status", "status-");

/* ---- toasts ---------------------------------------------- */
function toast(msg, kind = "") {
  const t = el("div", { class: `toast ${kind}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity 300ms"; }, 3200);
  setTimeout(() => t.remove(), 3600);
}

/* ---- shared state ---------------------------------------- */
const state = { reviews: [], hitl: [], cost: null };

/* ---- connection indicator -------------------------------- */
async function ping() {
  const dot = $("#conn-dot"), txt = $("#conn-text");
  try {
    await api("/health");
    dot.className = "dot ok"; txt.textContent = "connected";
  } catch (_) {
    dot.className = "dot bad"; txt.textContent = "offline";
  }
}

/* ============================================================
   VIEWS
   ============================================================ */
const TITLES = {
  overview: ["Overview", "Health of the review pipeline at a glance"],
  reviews: ["Reviews", "Every merge-request review, newest first"],
  hitl: ["HITL Queue", "Reviews awaiting a human decision"],
  cost: ["Cost", "Today's LLM spend against the daily budget"],
};

function setLoading() {
  $("#content").replaceChildren(el("div", { class: "spinner" }));
}

/* ---- Overview -------------------------------------------- */
async function viewOverview() {
  setLoading();
  const [reviews, hitl, cost] = await Promise.all([
    api("/reviews?limit=50"),
    api("/hitl/pending").catch(() => []),
    api("/cost/today").catch(() => null),
  ]);
  state.reviews = reviews; state.hitl = hitl; state.cost = cost;
  updateHitlCount(hitl.length);

  const spend = cost ? cost.spend_usd : 0;
  const budget = cost ? cost.budget_usd : 0;
  const pct = budget ? Math.min(100, (spend / budget) * 100) : 0;
  const meterColor = pct > 90 ? "var(--danger)" : pct > 70 ? "var(--warn)" : "var(--ok)";

  const kpis = el("div", { class: "kpi-grid" },
    kpi("Total reviews", reviews.length, "in the last 50 records"),
    kpi("Pending HITL", hitl.length, hitl.length ? "need your decision" : "queue is clear", hitl.length ? "kpi-accent" : ""),
    kpiCost("Spend today", fmtUSD(spend), `of ${fmtUSD(budget)} budget`, pct, meterColor),
    kpi("Reviews cost", fmtUSD(reviews.reduce((a, r) => a + (r.cost_usd || 0), 0)), "sum of shown reviews"),
  );

  // findings-by-severity requires detail; instead show status distribution from the list
  const byStatus = {};
  reviews.forEach((r) => { byStatus[r.status] = (byStatus[r.status] || 0) + 1; });
  const statusChips = Object.entries(byStatus).sort((a, b) => b[1] - a[1])
    .map(([s, n]) => el("div", { class: "meta-item" },
      el("div", { class: "k" }, statusBadge(s)),
      el("div", { class: "v" }, `${n} review${n > 1 ? "s" : ""}`)));

  const recent = reviews.slice(0, 6);

  $("#content").replaceChildren(
    kpis,
    el("div", { class: "card", style: "padding:24px;margin-bottom:24px" },
      el("div", { class: "section-head", style: "margin-top:0" }, el("h2", {}, "Review outcomes")),
      statusChips.length
        ? el("div", { class: "meta-grid" }, ...statusChips)
        : el("div", { class: "empty" }, "No reviews recorded yet.")),
    el("div", { class: "section-head" },
      el("h2", {}, "Recent reviews"),
      el("a", { href: "#/reviews", class: "btn ghost" }, "View all →")),
    reviewsTable(recent),
  );
}

const kpi = (label, value, sub, cls = "") =>
  el("div", { class: "card kpi" },
    el("div", { class: "kpi-label" }, label),
    el("div", { class: `kpi-value ${cls}` }, String(value)),
    el("div", { class: "kpi-sub" }, sub));

const kpiCost = (label, value, sub, pct, color) =>
  el("div", { class: "card kpi" },
    el("div", { class: "kpi-label" }, label),
    el("div", { class: "kpi-value" }, value),
    el("div", { class: "meter", style: "margin:4px 0 2px" },
      el("span", { style: `width:${pct}%;background:${color}` })),
    el("div", { class: "kpi-sub" }, sub));

/* ---- Reviews list ---------------------------------------- */
async function viewReviews() {
  setLoading();
  const reviews = await api("/reviews?limit=100");
  state.reviews = reviews;
  $("#content").replaceChildren(
    reviews.length
      ? reviewsTable(reviews)
      : emptyState("☰", "No reviews yet", "Reviews appear here once the agent runs on an MR."));
}

function reviewsTable(reviews) {
  if (!reviews.length) return emptyState("☰", "Nothing here yet", "");
  const rows = reviews.map((r) =>
    el("tr", { onclick: () => openReviewDrawer(r.id) },
      el("td", { class: "mono" }, "#" + r.id),
      el("td", {}, el("span", { class: "mono" }, `${r.project_id} · !${r.mr_iid}`)),
      el("td", {}, el("span", { class: "chip" }, r.scope || "all")),
      el("td", {}, statusBadge(r.status)),
      el("td", {}, r.triggered_by || "—"),
      el("td", { class: "mono" }, fmtUSD(r.cost_usd)),
      el("td", { class: "mono" }, fmtTime(r.created_at)),
    ));
  return el("div", { class: "card table-wrap" },
    el("table", {},
      el("thead", {}, el("tr", {},
        ...["Review", "Project · MR", "Scope", "Status", "Triggered by", "Cost", "Created"]
          .map((h) => el("th", {}, h)))),
      el("tbody", {}, ...rows)));
}

/* ---- HITL queue ------------------------------------------ */
async function viewHitl() {
  setLoading();
  const hitl = await api("/hitl/pending");
  state.hitl = hitl;
  updateHitlCount(hitl.length);
  if (!hitl.length) {
    $("#content").replaceChildren(emptyState("✓", "Queue is clear", "No reviews are waiting for a human decision."));
    return;
  }
  $("#content").replaceChildren(...hitl.map(hitlCard));
}

function hitlCard(r) {
  const findings = (r.findings || []).slice().sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  const card = el("div", { class: "card hitl-card" },
    el("div", { class: "hitl-card-head" },
      el("div", {},
        el("div", { style: "font-weight:700;font-size:15px" }, `Project ${r.project_id} · MR !${r.mr_iid}`),
        el("div", { class: "muted", style: "color:var(--text-faint);font-size:12px;margin-top:2px" },
          `Review #${r.id} · ${r.scope || "all"} · ${findings.length} finding${findings.length !== 1 ? "s" : ""} · ${fmtUSD(r.cost_usd)}`)),
      el("div", { class: "hitl-actions" },
        el("button", { class: "btn primary", onclick: () => decide("approve", r.id, card) }, "✓ Approve & post"),
        el("button", { class: "btn danger", onclick: () => decide("reject", r.id, card) }, "Reject"))),
    ...findings.map(findingCard),
  );
  return card;
}

function findingCard(f) {
  const conf = f.confidence != null ? Math.round(f.confidence * 100) : null;
  return el("div", { class: "finding" },
    el("div", { class: "finding-head" },
      sevBadge(f.severity),
      el("span", { class: "chip" }, f.agent_type || "?"),
      f.category ? el("span", { class: "chip" }, f.category) : null,
      el("span", { class: "finding-title" }, f.title || "(untitled finding)"),
      f.file_path ? el("span", { class: "finding-loc" },
        `${f.file_path}${f.line_start ? ":" + f.line_start : ""}${f.line_end && f.line_end !== f.line_start ? "-" + f.line_end : ""}`) : null),
    f.rationale ? el("div", { class: "finding-body" }, f.rationale) : null,
    el("div", { class: "finding-foot" },
      conf != null ? el("span", { style: "font-size:12px;color:var(--text-faint)" },
        "confidence ", el("span", { class: "confidence-bar" }, el("span", { style: `width:${conf}%` })), " ", conf + "%") : null,
      el("button", { class: "btn ghost", style: "margin-left:auto;font-size:12px;padding:5px 10px",
        onclick: (e) => { e.stopPropagation(); disputeFinding(f.id); } }, "⚑ Dispute")),
  );
}

async function decide(action, reviewId, cardEl) {
  const reason = action === "reject"
    ? (prompt("Reason for rejecting (optional):") ?? "") : "";
  if (action === "reject" && reason === null) return;
  const btns = cardEl.querySelectorAll("button");
  btns.forEach((b) => (b.disabled = true));
  try {
    const body = JSON.stringify({ decided_by: "dashboard", reason });
    const res = await api(`/hitl/${reviewId}/${action}`, { method: "POST", body });
    toast(action === "approve"
      ? `Approved — ${res.posted ?? ""} findings posted to the MR`.trim()
      : "Review rejected", "ok");
    cardEl.style.transition = "opacity 250ms, transform 250ms";
    cardEl.style.opacity = "0"; cardEl.style.transform = "translateX(12px)";
    setTimeout(() => {
      cardEl.remove();
      state.hitl = state.hitl.filter((r) => r.id !== reviewId);
      updateHitlCount(state.hitl.length);
      if (!state.hitl.length && location.hash.includes("hitl")) viewHitl();
    }, 260);
  } catch (e) {
    toast("Failed: " + e.message, "err");
    btns.forEach((b) => (b.disabled = false));
  }
}

async function disputeFinding(findingId) {
  const comment = prompt("Why is this a false positive? (optional)");
  if (comment === null) return;
  try {
    await api(`/findings/${findingId}/dispute`, {
      method: "POST",
      body: JSON.stringify({ comment, created_by: "dashboard" }),
    });
    toast("Dispute recorded", "ok");
  } catch (e) {
    toast("Failed: " + e.message, "err");
  }
}

/* ---- Cost ------------------------------------------------ */
async function viewCost() {
  setLoading();
  const [cost, reviews] = await Promise.all([
    api("/cost/today"),
    api("/reviews?limit=100").catch(() => []),
  ]);
  const spend = cost.spend_usd, budget = cost.budget_usd;
  const pct = budget ? Math.min(100, (spend / budget) * 100) : 0;
  const color = pct > 90 ? "var(--danger)" : pct > 70 ? "var(--warn)" : "var(--ok)";
  const remaining = Math.max(0, budget - spend);

  $("#content").replaceChildren(
    el("div", { class: "kpi-grid" },
      kpi("Spent today", fmtUSD(spend), "across all reviews"),
      kpi("Daily budget", fmtUSD(budget), "configured via DAILY_BUDGET_USD"),
      kpi("Remaining", fmtUSD(remaining), pct >= 100 ? "budget exhausted" : `${(100 - pct).toFixed(0)}% left`,
        pct >= 100 ? "kpi-accent" : "")),
    el("div", { class: "card", style: "padding:28px" },
      el("div", { class: "section-head", style: "margin-top:0" },
        el("h2", {}, "Budget usage"),
        el("span", { class: "mono", style: "color:var(--text-dim)" }, pct.toFixed(1) + "%")),
      el("div", { class: "meter", style: "height:14px" }, el("span", { style: `width:${pct}%;background:${color}` })),
      el("div", { style: "display:flex;justify-content:space-between;margin-top:8px;color:var(--text-faint);font-size:12px" },
        el("span", {}, fmtUSD(spend)), el("span", {}, fmtUSD(budget)))),
    reviews.length ? el("div", { style: "margin-top:24px" },
      el("div", { class: "section-head" }, el("h2", {}, "Cost by review")),
      reviewsTable(reviews.slice().sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 15))) : null,
  );
}

/* ---- Review drawer (detail + trace) ---------------------- */
async function openReviewDrawer(id) {
  const drawer = $("#drawer"), scrim = $("#drawer-scrim");
  scrim.hidden = false; drawer.hidden = false; drawer.setAttribute("aria-hidden", "false");
  drawer.replaceChildren(el("div", { class: "spinner" }));

  try {
    const [review, events] = await Promise.all([
      api(`/reviews/${id}`),
      api(`/reviews/${id}/events`).catch(() => []),
    ]);
    renderDrawer(review, events);
  } catch (e) {
    drawer.replaceChildren(el("div", { class: "empty" }, "Failed to load: " + e.message));
  }
}

function renderDrawer(r, events) {
  const findings = (r.findings || []).slice().sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));

  const body = el("div", { class: "drawer-body" });
  const findingsPane = el("div", {},
    findings.length
      ? el("div", {}, ...findings.map(findingCard))
      : el("div", { class: "empty" }, "No findings recorded for this review."));

  const tracePane = el("div", { hidden: true },
    events.length
      ? el("div", { class: "trace" }, ...events.map((e) =>
          el("div", { class: "trace-row" },
            el("span", { class: "trace-time" }, fmtTime(e.ts)),
            el("span", {},
              el("span", { class: "chip" }, e.agent_type || e.event_type),
              " ", el("span", { style: "color:var(--text-dim);font-size:12px" }, e.model || e.event_type),
              e.outcome ? " " : null, e.outcome ? statusBadge(e.outcome) : null),
            el("span", { class: "trace-metrics" },
              `${fmtNum(e.tokens_in)}→${fmtNum(e.tokens_out)} tok · ${e.latency_ms ?? "—"}ms · ${e.cost_usd != null ? fmtUSD(e.cost_usd) : "—"}`))))
      : el("div", { class: "empty" }, "No trace events recorded."));

  const tabs = el("div", { class: "tabs" },
    el("div", { class: "tab active", onclick: (ev) => switchTab(ev, findingsPane, tracePane) }, `Findings (${findings.length})`),
    el("div", { class: "tab", onclick: (ev) => switchTab(ev, tracePane, findingsPane) }, `Trace (${events.length})`));

  body.append(
    el("div", { class: "meta-grid" },
      metaItem("Status", statusBadge(r.status)),
      metaItem("Scope", el("span", { class: "chip" }, r.scope || "all")),
      metaItem("Triggered by", r.triggered_by || "—"),
      metaItem("Cost", el("span", { class: "mono" }, fmtUSD(r.cost_usd))),
      metaItem("Created", fmtTime(r.created_at))),
    r.summary ? el("div", { class: "summary-box" }, r.summary) : null,
    tabs, findingsPane, tracePane,
  );

  $("#drawer").replaceChildren(
    el("div", { class: "drawer-head" },
      el("div", {},
        el("h2", {}, `Review #${r.id}`),
        el("div", { style: "color:var(--text-faint);font-family:var(--mono);font-size:12px" },
          `Project ${r.project_id} · MR !${r.mr_iid}`)),
      el("button", { class: "drawer-close", onclick: closeDrawer }, "×")),
    body);
}

const metaItem = (k, v) => el("div", { class: "meta-item" },
  el("div", { class: "k" }, k), el("div", { class: "v" }, v));

function switchTab(ev, show, hide) {
  document.querySelectorAll(".drawer .tab").forEach((t) => t.classList.remove("active"));
  ev.currentTarget.classList.add("active");
  show.hidden = false; hide.hidden = true;
}

function closeDrawer() {
  const drawer = $("#drawer"), scrim = $("#drawer-scrim");
  drawer.hidden = true; scrim.hidden = true; drawer.setAttribute("aria-hidden", "true");
}

/* ---- shared UI bits -------------------------------------- */
const emptyState = (ico, title, sub) =>
  el("div", { class: "card empty", style: "padding:56px 24px" },
    el("div", { class: "empty-ico" }, ico),
    el("div", { style: "font-weight:700;color:var(--text);font-size:16px" }, title),
    sub ? el("div", { style: "margin-top:6px" }, sub) : null);

function updateHitlCount(n) {
  const b = $("#hitl-count");
  b.textContent = n;
  b.hidden = !n;
}

/* ============================================================
   ROUTER
   ============================================================ */
const ROUTES = {
  overview: viewOverview,
  reviews: viewReviews,
  hitl: viewHitl,
  cost: viewCost,
};

async function route() {
  const name = (location.hash.replace("#/", "") || "overview").split("/")[0];
  const view = ROUTES[name] || viewOverview;
  const [title, sub] = TITLES[name] || TITLES.overview;
  $("#view-title").textContent = title;
  $("#view-sub").textContent = sub;
  document.querySelectorAll(".nav-item").forEach((a) =>
    a.classList.toggle("active", a.dataset.route === name));
  closeDrawer();
  try { await view(); }
  catch (e) { $("#content").replaceChildren(emptyState("⚠", "Could not load", e.message)); }
}

/* ---- theme ----------------------------------------------- */
function initTheme() {
  const saved = localStorage.getItem("mr-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  $("#theme-btn").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("mr-theme", next);
  });
}

/* ---- boot ------------------------------------------------ */
function boot() {
  initTheme();
  $("#refresh-btn").addEventListener("click", route);
  $("#drawer-scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  window.addEventListener("hashchange", route);
  ping();
  setInterval(ping, 20000);
  route();
}

document.addEventListener("DOMContentLoaded", boot);

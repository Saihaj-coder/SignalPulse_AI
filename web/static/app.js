/* SignalPulse AI — console client */

const state = {
  focus: "all",
  view: "hub",
  overview: null,
  messages: [],
  citations: [],
  charts: {},
};

const SUGGESTED = {
  all: [
    ["KEVs this week", "Do we have any newly listed known-exploited vulnerabilities we should prioritize this week?"],
    ["CVE deep-dive", "What is CVE-2026-56291, what product does it affect, and what's the remediation deadline?"],
    ["BOD / KEV actions", "Are there CISA-required actions or BOD guidance tied to the latest KEV entries?"],
    ["NIST risk", "According to NIST, how should we structure cybersecurity risk management for a federal or state project?"],
    ["NIST CSF 2.0", "What does NIST CSF 2.0 say organizations should use it for?"],
    ["CMS notices", "Are there any recent CMS Medicare/Medicaid program notices we should be aware of?"],
    ["HealthIT / ONC", "What's in the latest HealthIT.gov / ONC news about health information technology programs or awards?"],
    ["State CIO priorities", "What are the current NASCIO state CIO top priorities we should know about?"],
  ],
  cyber: [
    ["KEVs this week", "Do we have any newly listed known-exploited vulnerabilities we should prioritize this week?"],
    ["CVE deep-dive", "What is CVE-2026-56291, what product does it affect, and what's the remediation deadline?"],
    ["BOD actions", "Are there CISA-required actions or BOD guidance tied to the latest KEV entries?"],
    ["Upload CVEs", "Which recent CVEs in our sources look relevant to web apps or file-upload vulnerabilities?"],
  ],
  nist: [
    ["Risk management", "According to NIST, how should we structure cybersecurity risk management for a federal or state project?"],
    ["CSF 2.0", "What does NIST CSF 2.0 say organizations should use it for?"],
    ["SP 800-53 access", "What does NIST SP 800-53 say about access-control policy and account management?"],
    ["RMF overview", "Where can I find NIST's Risk Management Framework overview in our sources?"],
  ],
  health: [
    ["CMS notices", "Are there any recent CMS Medicare/Medicaid program notices we should be aware of?"],
    ["HealthIT / ONC", "What's in the latest HealthIT.gov / ONC news about health information technology programs or awards?"],
    ["FDA digital health", "What FDA or HHS digital-health related items appear in our recent sources?"],
  ],
  defense: [
    ["DoD FR notices", "What recent Defense Department Federal Register notices are in our corpus?"],
    ["DoD privacy", "Are there DoD privacy or records notices we should know about from ingested sources?"],
  ],
  state: [
    ["NASCIO priorities", "What are the current NASCIO state CIO top priorities we should know about?"],
    ["AI priority?", "Is AI or emerging technology listed among NASCIO state CIO priorities?"],
  ],
};

const CHART_COLORS = [
  "#1b4f72", "#0f766e", "#6d28d9", "#c2410c", "#334155",
  "#1d4ed8", "#0891b2", "#a16207", "#be123c", "#475569",
];

function $(id) {
  return document.getElementById(id);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function setLoader(on) {
  $("loader").classList.toggle("show", !!on);
}

function badgeClass(agency) {
  const a = (agency || "").toLowerCase();
  if (a.includes("cisa") || a.includes("nvd")) return "cyber";
  if (a.includes("nist")) return "nist";
  if (a.includes("health") || a.includes("hhs") || a.includes("cms") || a.includes("onc") || a.includes("fda")) return "health";
  if (a.includes("nascio") || a.includes("state")) return "state";
  if (a.includes("defense") || a.includes("dod")) return "def";
  return "def";
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderMarkdown(md) {
  if (window.marked) {
    return marked.parse(md || "");
  }
  return `<p>${escapeHtml(md || "")}</p>`;
}

function setView(name) {
  state.view = name;
  document.body.classList.toggle("view-ask", name === "ask");
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${name}`);
  });
  if (name === "corpus") loadCorpus();
  if (name === "pipeline") loadPipeline();
  if (name === "ask") {
    renderChips("askChips");
    const input = $("chatAsk");
    if (input) {
      setTimeout(() => input.focus(), 50);
    }
  }
}

function setFocus(focus) {
  state.focus = focus;
  document.querySelectorAll("#agencyRow .pill").forEach((p) => {
    p.classList.toggle("active", p.dataset.focus === focus);
  });
  renderChips("hubChips");
  renderChips("askChips");
  loadHubDocs();
  if (state.view === "corpus") loadCorpus();
}

function renderChips(elId) {
  const el = $(elId);
  if (!el) return;
  const items = SUGGESTED[state.focus] || SUGGESTED.all;
  el.innerHTML = items
    .map(
      ([label, q]) =>
        `<button type="button" class="chip" data-q="${escapeHtml(q)}">${escapeHtml(label)}</button>`
    )
    .join("");
  el.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => runAsk(btn.dataset.q));
  });
}

function destroyChart(key) {
  if (state.charts[key]) {
    state.charts[key].destroy();
    delete state.charts[key];
  }
}

function makeBar(canvasId, key, rows, horizontal = true) {
  destroyChart(key);
  const ctx = $(canvasId);
  if (!ctx || !window.Chart) return;
  const labels = rows.map((r) => r.label);
  const data = rows.map((r) => r.value);
  state.charts[key] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          data,
          backgroundColor: CHART_COLORS.slice(0, data.length),
          borderRadius: 6,
          borderSkipped: false,
        },
      ],
    },
    options: {
      indexAxis: horizontal ? "y" : "x",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: "#edf1f5" }, ticks: { color: "#5b6b7c" } },
        y: { grid: { display: false }, ticks: { color: "#243447", font: { size: 11 } } },
      },
    },
  });
}

function makeDoughnut(canvasId, key, rows) {
  destroyChart(key);
  const ctx = $(canvasId);
  if (!ctx || !window.Chart) return;
  state.charts[key] = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: rows.map((r) => r.label),
      datasets: [
        {
          data: rows.map((r) => r.value),
          backgroundColor: CHART_COLORS,
          borderWidth: 2,
          borderColor: "#fff",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "right",
          labels: { boxWidth: 12, color: "#243447", font: { size: 11 } },
        },
      },
      cutout: "58%",
    },
  });
}

function renderKpis(kpis) {
  const items = [
    ["#1d4ed8", kpis.documents, "Documents"],
    ["#0f766e", kpis.chunks, "Passages"],
    ["#6d28d9", kpis.entities, "Entities"],
    ["#c2410c", kpis.relationships, "Graph edges"],
  ];
  $("kpiRow").innerHTML = items
    .map(
      ([c, n, l]) => `
      <div class="kpi">
        <div class="meta"><i style="background:${c}"></i>${l}</div>
        <div class="n">${n.toLocaleString()}</div>
      </div>`
    )
    .join("");
}

function renderIngest(ingest) {
  if (!ingest) {
    $("ingestMetrics").innerHTML = `<div class="empty">No ingest stamp yet. Run <code>run_demo_ingest.ps1</code>.</div>`;
    $("ingestSources").textContent = "";
    return;
  }
  const r = ingest.report;
  $("ingestSub").textContent = `Profile “${ingest.profile}” · finished ${ingest.finished_at}`;
  const cells = [
    [r.new, "New"],
    [r.updated, "Updated"],
    [r.skipped, "Skipped"],
    [r.failed, "Failed"],
    [r.chunks, "Chunks"],
    [r.entity_mentions, "Mentions"],
    [r.relationships, "Rel links"],
    [`${Math.round(r.seconds / 60)}m`, "Runtime"],
  ];
  $("ingestMetrics").innerHTML = cells
    .map(([v, l]) => `<div class="metric"><div class="v">${v}</div><div class="l">${l}</div></div>`)
    .join("");
  $("ingestSources").textContent = `Feeds: ${(ingest.sources || []).join(" · ")}`;
}

function docRows(docs, { showDomain = false } = {}) {
  if (!docs.length) {
    return `<tr><td colspan="6" class="muted" style="padding:1rem">No documents for this filter.</td></tr>`;
  }
  return docs
    .map((d) => {
      const askQ = `What are the key points delivery teams should know from: ${d.title}?`;
      return `<tr>
        <td class="muted">${escapeHtml(d.published)}</td>
        <td><span class="badge ${badgeClass(d.agency)}">${escapeHtml(d.agency)}</span></td>
        ${showDomain ? `<td class="muted">${escapeHtml(d.domain)}</td>` : ""}
        <td class="title-cell">${
          d.url
            ? `<a href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.title)}</a>`
            : escapeHtml(d.title)
        }${d.host ? `<div class="muted">${escapeHtml(d.host)}</div>` : ""}</td>
        <td>${d.chunks}</td>
        <td class="row-actions"><button type="button" class="btn btn-primary" data-ask="${escapeHtml(askQ)}">Ask</button></td>
      </tr>`;
    })
    .join("");
}

async function loadHubDocs() {
  try {
    const data = await api(`/api/documents?focus=${encodeURIComponent(state.focus)}&limit=25`);
    $("hubDocsBody").innerHTML = docRows(data.documents);
    $("hubDocsBody").querySelectorAll("[data-ask]").forEach((btn) => {
      btn.addEventListener("click", () => runAsk(btn.dataset.ask));
    });
  } catch (err) {
    $("hubDocsBody").innerHTML = `<tr><td colspan="5" class="muted">${escapeHtml(err.message)}</td></tr>`;
  }
}

async function loadOverview() {
  const ov = await api("/api/overview");
  state.overview = ov;
  const dot = $("syncDot");
  dot.classList.toggle("off", !ov.online);
  $("syncLabel").innerHTML = `<span class="sync-dot${ov.online ? "" : " off"}" id="syncDot"></span>${escapeHtml(ov.sync_label)}`;

  const k = ov.kpis;
  $("hubInsight").innerHTML = ov.online
    ? `<b>Corpus live.</b> ${k.documents} documents · ${k.chunks} searchable passages · ${k.entities} entities · ${k.mentions} mentions across ${k.feeds || "—"} feeds from the last demo ingest.`
    : `<b>Corpus offline.</b> Start Neo4j with <code>start_neo4j.ps1</code>, then refresh.`;

  renderKpis(k);
  renderIngest(ov.ingest);
  makeBar("chartAgency", "agency", ov.by_agency || []);
  makeDoughnut("chartDomain", "domain", ov.by_domain || []);
  makeBar("chartEntities", "entities", ov.entity_types || []);
  await loadHubDocs();
}

async function loadCorpus() {
  const q = $("corpusSearch").value.trim();
  const data = await api(
    `/api/documents?focus=${encodeURIComponent(state.focus)}&q=${encodeURIComponent(q)}&limit=120`
  );
  $("corpusBody").innerHTML = docRows(data.documents, { showDomain: true });
  $("corpusBody").querySelectorAll("[data-ask]").forEach((btn) => {
    btn.addEventListener("click", () => runAsk(btn.dataset.ask));
  });
}

function renderDigest(data) {
  const box = $("digestBox");
  if (!data.available) {
    box.innerHTML = `<div class="empty">No digest yet — it is generated automatically after each ingest run.</div>`;
    return;
  }
  const d = data.digest;
  const when = (d.generated_at || "").replace("T", " ").slice(0, 16);
  const alertCount = d.alerts?.length || 0;

  const metrics = [
    [d.new, "New docs"],
    [d.updated, "Updated"],
    [alertCount, "Alerts"],
    [(data.watchlist || []).length, "Watch terms"],
  ]
    .map(([v, l]) => `<div class="metric"><div class="v">${v}</div><div class="l">${l}</div></div>`)
    .join("");

  const chips = (data.watchlist || [])
    .map((k) => `<span class="digest-chip">${escapeHtml(k)}</span>`)
    .join("");

  let alertsHtml;
  if (alertCount) {
    alertsHtml = d.alerts
      .map(
        (a) => `<div class="digest-alert">
          <div>
            <div class="name">${
              a.url
                ? `<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${escapeHtml(a.title)}</a>`
                : escapeHtml(a.title)
            }</div>
            <div class="meta">${escapeHtml(a.agency || "")} · matched <span class="kw">${escapeHtml((a.watchlist_hits || []).join(", "))}</span></div>
          </div>
          ${a.url ? `<a class="open" href="${escapeHtml(a.url)}" target="_blank" rel="noopener">Open ↗</a>` : ""}
        </div>`
      )
      .join("");
  } else {
    alertsHtml = `<div class="muted">No watchlist matches in the last run.</div>`;
  }

  const changeRow = (c) => `<div class="digest-row">
    <span class="digest-status${c.status === "new" ? "" : " upd"}">${c.status === "new" ? "NEW" : "UPD"}</span>
    <span class="badge ${badgeClass(c.agency)}">${escapeHtml(c.agency || "—")}</span>
    <span class="digest-title">${
      c.url
        ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</a>`
        : escapeHtml(c.title)
    }</span>
  </div>`;

  const changesHtml = (d.by_domain || [])
    .map(
      (g) => `<div class="digest-group">
        <div class="domain">${escapeHtml(g.domain)}</div>
        ${g.items.map(changeRow).join("")}
      </div>`
    )
    .join("");

  box.innerHTML = `
    <div class="digest-sub">Run ${escapeHtml(when)} UTC · profile “${escapeHtml(d.profile)}” · ${d.total_changes} change(s)</div>
    <div class="digest-metrics">${metrics}</div>
    <div class="digest-watch"><span class="label">Watchlist</span>${chips || '<span class="muted">empty — add terms to data/seeds/watchlist.txt</span>'}</div>
    <div class="digest-h">Watchlist alerts</div>
    ${alertsHtml}
    ${
      d.total_changes
        ? `<div class="digest-h">All changes in this run</div>${changesHtml}`
        : `<div class="muted">No new or updated documents in the last run — the corpus was already current.</div>`
    }
  `;
}

async function loadPipeline() {
  const [pipe, ov, dig] = await Promise.all([
    api("/api/pipeline"),
    api("/api/sources"),
    api("/api/digest"),
  ]);
  renderDigest(dig);
  $("stages").innerHTML = pipe.stages
    .map(
      (s) => `<div class="stage"><div class="n">0${s.n}</div><div class="name">${escapeHtml(s.name)}</div><div class="detail">${escapeHtml(s.detail)}</div></div>`
    )
    .join("");
  $("sourceGrid").innerHTML = ov.sources
    .map(
      (s) => `<div class="source-card">
        <div>
          <div class="name">${escapeHtml(s.name)}</div>
          <div class="meta">Tier ${s.tier} · ${escapeHtml(s.format)} · ${escapeHtml(s.domain)}</div>
        </div>
        <div style="text-align:right">
          <div class="tag${s.in_last_ingest ? " on" : ""}">${s.in_last_ingest ? "In last run" : "Idle"}</div>
          <div class="muted" style="margin-top:0.35rem">${s.approx_docs} docs*</div>
        </div>
      </div>`
    )
    .join("");

  if (state.overview?.rel_types) {
    makeBar("chartRels", "rels", state.overview.rel_types, false);
  } else {
    const overview = await api("/api/overview");
    state.overview = overview;
    makeBar("chartRels", "rels", overview.rel_types || [], false);
  }
}

function renderChat() {
  const el = $("chatStream");
  if (!state.messages.length) {
    el.innerHTML = `<div class="empty">Ask a question below, or try a starter above.</div>`;
  } else {
    el.innerHTML = state.messages
      .map((m) => {
        if (m.role === "user") {
          return `<div class="msg user"><div class="who">You</div><div class="body">${escapeHtml(m.content)}</div></div>`;
        }
        const grounded = (!m.refused && m.tools?.length)
          ? `<div class="tools">Answer grounded in retrieved sources</div>`
          : "";
        return `<div class="msg"><div class="who">SignalPulse</div><div class="body">${renderMarkdown(m.content)}</div>${grounded}</div>`;
      })
      .join("");
    el.scrollTop = el.scrollHeight;
  }

  const list = $("citeList");
  const empty = $("citeEmpty");
  if (!state.citations.length) {
    list.innerHTML = "";
    empty.style.display = "block";
  } else {
    empty.style.display = "none";
    list.innerHTML = state.citations
      .map((u) => `<li><a href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(u)}</a></li>`)
      .join("");
  }
}

async function runAsk(question) {
  const q = (question || "").trim();
  if (!q) return;
  setView("ask");
  state.messages.push({ role: "user", content: q });
  renderChat();
  setLoader(true);
  const buttons = ["globalAskBtn", "chatAskBtn"].map((id) => $(id)).filter(Boolean);
  buttons.forEach((b) => {
    b.disabled = true;
  });
  try {
    const reply = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question: q, focus: state.focus }),
    });
    state.messages.push({
      role: "assistant",
      content: reply.answer,
      tools: reply.tools || [],
      refused: !!reply.refused,
    });
    for (const u of reply.urls || []) {
      if (!state.citations.includes(u)) state.citations.push(u);
    }
  } catch (err) {
    state.messages.push({
      role: "assistant",
      content: `**Could not complete this request.**\n\n\`${err.message}\`\n\nConfirm Neo4j is running and try again.`,
      tools: [],
    });
  } finally {
    setLoader(false);
    buttons.forEach((b) => {
      b.disabled = false;
    });
    renderChat();
    const input = $("chatAsk");
    if (input) input.focus();
  }
}

function wire() {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });
  document.querySelectorAll("#agencyRow .pill").forEach((pill) => {
    pill.addEventListener("click", () => setFocus(pill.dataset.focus));
  });
  $("globalAskForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("globalAsk").value;
    $("globalAsk").value = "";
    runAsk(q);
  });
  $("chatAskForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("chatAsk").value;
    $("chatAsk").value = "";
    runAsk(q);
  });
  $("chatAsk").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("chatAskForm").requestSubmit();
    }
  });
  const openAsk = () => setView("ask");
  ["hubOpenAsk", "aboutOpenAsk"].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("click", openAsk);
  });
  $("clearChat").addEventListener("click", () => {
    state.messages = [];
    state.citations = [];
    renderChat();
  });
  $("corpusRefresh").addEventListener("click", () => loadCorpus());
  $("corpusSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadCorpus();
  });
  renderChips("hubChips");
  renderChips("askChips");
  renderChat();
}

async function boot() {
  wire();
  try {
    await loadOverview();
  } catch (err) {
    $("hubInsight").textContent = `Failed to load overview: ${err.message}`;
  }
}

document.addEventListener("DOMContentLoaded", boot);

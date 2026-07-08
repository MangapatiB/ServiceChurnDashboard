const bootstrap = window.__DASHBOARD_BOOTSTRAP__ || {};
const refreshButton = document.getElementById("refresh-button");
const filtersForm = document.getElementById("dashboard-filters");
const segmentInput = document.getElementById("segment-input");
const locationInput = document.getElementById("location-input");
const limitInput = document.getElementById("limit-input");
const refreshIntervalInput = document.getElementById("refresh-interval-select");
const customerPrevButton = document.getElementById("customer-prev-button");
const customerNextButton = document.getElementById("customer-next-button");
const customerPaginationSummary = document.getElementById("customer-pagination-summary");
const customerSortInput = document.getElementById("customer-sort-select");

const state = {
  endpoint: "/api/dashboard",
  refreshSeconds: Number(bootstrap.refreshSeconds || 60),
  segment: bootstrap.currentSegment || "res",
  location: bootstrap.currentLocation || "",
  limit: bootstrap.currentLimit || 12,
  maxLimit: Number(bootstrap.maxDashboardLimit || 1000),
  customerPage: Math.max(Number(bootstrap.currentCustomerPage || 1) - 1, 0),
  customerPageSize: Number(bootstrap.currentCustomerPageSize || 15),
  customerSort: bootstrap.currentCustomerSort || customerSortInput?.value || "desc",
  snapshot: bootstrap.initialSnapshot || null,
  refreshTimerId: null,
  isFetching: false,
  pageCache: new Map(),
};

function buildSnapshotCacheKey(pageIndex = state.customerPage) {
  return JSON.stringify({
    segment: state.segment,
    location: state.location,
    limit: clampLimit(state.limit),
    customer_page: Number(pageIndex) + 1,
    customer_page_size: Number(state.customerPageSize),
    customer_sort: state.customerSort,
  });
}

function clearSnapshotCache() {
  state.pageCache.clear();
}

function clampLimit(value) {
  const parsed = Number(value || state.limit || 12);
  if (!Number.isFinite(parsed)) {
    return Math.min(12, state.maxLimit);
  }
  return Math.max(1, Math.min(parsed, state.maxLimit));
}

function getSortedCustomers(items) {
  const direction = state.customerSort === "asc" ? 1 : -1;
  return [...items].sort((left, right) => {
    const leftValue = Number(left.churn_probability || 0);
    const rightValue = Number(right.churn_probability || 0);
    if (leftValue === rightValue) {
      return String(left.customer_id || "").localeCompare(String(right.customer_id || ""));
    }
    return (leftValue - rightValue) * direction;
  });
}

function setAutoRefreshInterval(nextRefreshSeconds) {
  state.refreshSeconds = Number(nextRefreshSeconds || 0);
  if (state.refreshTimerId) {
    window.clearInterval(state.refreshTimerId);
    state.refreshTimerId = null;
  }
  if (state.refreshSeconds > 0) {
    state.refreshTimerId = window.setInterval(fetchSnapshot, state.refreshSeconds * 1000);
  }
}

function toneClass(tone) {
  if (tone === "risk") return "kpi-card--risk";
  if (tone === "good") return "kpi-card--good";
  return "kpi-card--warning";
}

function riskBadgeClass(tier) {
  if (tier === "Tier 1") return "badge badge--risk";
  if (tier === "Tier 2") return "badge badge--warning";
  return "badge badge--neutral";
}

function healthBadgeClass(status) {
  if (status === "Offline") return "badge badge--risk";
  if (status === "Unavailable") return "badge badge--warning";
  return "badge badge--good";
}

function customerRiskBadgeClass(score) {
  const numericScore = Number(score || 0);
  if (numericScore >= 90) return "badge badge--risk";
  if (numericScore >= 75) return "badge badge--warning";
  return "badge badge--neutral";
}

function formatChurnProbability(value) {
  const numericValue = Number(value || 0);
  return `${numericValue.toFixed(1)}%`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function tableEmptyRow(message, colspan) {
  return `
    <tr>
      <td colspan="${colspan}">
        <div class="empty-state empty-state--table">${escapeHtml(message)}</div>
      </td>
    </tr>
  `;
}

function renderEmptyState(container, message) {
  container.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function getPriorityMarket(items) {
  return [...items].sort((left, right) => {
    const leftPressure = Number(left.high_risk_count || 0) + Number(left.flagged_accounts || 0);
    const rightPressure = Number(right.high_risk_count || 0) + Number(right.flagged_accounts || 0);
    return rightPressure - leftPressure;
  })[0];
}

function getPrimarySignal(items) {
  return [...items].sort((left, right) => Number(right.value || 0) - Number(left.value || 0))[0];
}

function renderHeroHighlights(snapshot) {
  const container = document.getElementById("hero-highlights");
  if (!container) return;

  const topMarket = getPriorityMarket(snapshot.geo_summary || []);
  const topSignal = getPrimarySignal(snapshot.signal_mix || []);
  const topCustomer = [...(snapshot.high_risk_customers || [])].sort(
    (left, right) => Number(right.churn_probability || 0) - Number(left.churn_probability || 0),
  )[0];

  const cards = [
    {
      label: "Most exposed market",
      value: topMarket ? topMarket.geo : "No market data",
      meta: topMarket ? `${topMarket.high_risk_count} accounts in 90+ risk` : "Waiting for snapshot data",
    },
    {
      label: "Primary driver",
      value: topSignal ? topSignal.label : "No signal data",
      meta: topSignal ? `${topSignal.value}% of observed friction` : "Waiting for snapshot data",
    },
    {
      label: "Highest priority account",
      value: topCustomer ? topCustomer.customer_id : "No customer data",
      meta: topCustomer ? `${topCustomer.geo} · churn ${formatChurnProbability(topCustomer.churn_probability)}` : "Waiting for snapshot data",
    },
  ];

  container.innerHTML = cards.map((item) => `
    <article class="hero-highlight">
      <p class="hero-highlight__label">${escapeHtml(item.label)}</p>
      <p class="hero-highlight__value">${escapeHtml(item.value)}</p>
      <p class="hero-highlight__meta">${escapeHtml(item.meta)}</p>
    </article>
  `).join("");
}

function renderSummaryStrip(snapshot) {
  const container = document.getElementById("summary-strip");
  if (!container) return;

  const meta = snapshot.meta || {};
  const topMarket = getPriorityMarket(snapshot.geo_summary || []);
  const topSignal = getPrimarySignal(snapshot.signal_mix || []);
  const response = (snapshot.playbooks || [])[0];

  const cards = [
    {
      label: "Focus market",
      title: topMarket ? topMarket.geo : "No market loaded",
      detail: topMarket
        ? `${topMarket.flagged_accounts} flagged accounts · ${topMarket.recommended_action}`
        : "Load or refresh the dashboard to populate geo focus.",
    },
    {
      label: "Signal to resolve",
      title: topSignal ? topSignal.label : "No signal loaded",
      detail: topSignal
        ? `${topSignal.value}% of current churn pressure is linked to this pattern.`
        : "Signal share will appear when the snapshot contains mix data.",
    },
    {
      label: "Operating posture",
      title: response ? response.title : "No playbook loaded",
      detail: response
        ? `${meta.status || "Status unknown"} data stream · ${response.detail}`
        : "Playbook guidance will appear when tier definitions are available.",
    },
  ];

  container.innerHTML = cards.map((item) => `
    <article class="summary-card">
      <p class="summary-card__label">${escapeHtml(item.label)}</p>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.detail)}</p>
    </article>
  `).join("");
}

function renderStatus(meta) {
  const source = document.getElementById("status-source");
  const message = document.getElementById("status-message");
  const location = document.getElementById("status-location");
  const updated = document.getElementById("status-updated");

  if (source) {
    source.textContent = `${meta.source || "unknown"} / ${meta.status || "unknown"}`;
  }
  if (message) {
    message.textContent = meta.message || "";
  }
  if (location) {
    const segmentLabel = (meta.customer_segment || state.segment || "res") === "com" ? "Business" : "Residential";
    location.textContent = `${meta.location || "ALL LOCATIONS"} · ${segmentLabel} · ${meta.limit || state.limit} accounts`;
  }
  if (updated) {
    updated.textContent = meta.last_updated || "n/a";
  }
}

function renderKpis(kpis) {
  const container = document.getElementById("kpi-grid");
  if (!kpis.length) {
    renderEmptyState(container, "No KPI metrics are available for the current filter.");
    return;
  }

  container.innerHTML = kpis.map((item) => `
    <article class="kpi-card ${toneClass(item.tone)}">
      <p class="kpi-card__label">${item.label}</p>
      <p class="kpi-card__value">${item.value}</p>
      <p class="kpi-card__delta">${item.delta || ""}</p>
    </article>
  `).join("");
}

function renderSignalMix(items) {
  const container = document.getElementById("signal-list");
  if (!items.length) {
    renderEmptyState(container, "No churn drivers are available for the current filter.");
    return;
  }

  const total = items.reduce((sum, item) => sum + Number(item.value || 0), 0) || 1;
  container.innerHTML = items.map((item) => {
    const width = Math.max(8, Math.round((Number(item.value || 0) / total) * 100));
    return `
      <div class="signal-row">
        <div>
          <p class="signal-row__label">${item.label}</p>
          <p class="signal-row__value">${item.value}%</p>
        </div>
        <div class="signal-row__track">
          <span class="signal-row__fill" style="width:${width}%"></span>
        </div>
      </div>
    `;
  }).join("");
}

function renderPlaybooks(items) {
  const container = document.getElementById("playbook-list");
  if (!container) {
    return;
  }
  if (!items.length) {
    renderEmptyState(container, "No response playbooks are available for the current snapshot.");
    return;
  }

  container.innerHTML = items.map((item) => `
    <article class="playbook-card">
      <p class="playbook-card__tier">${item.tier}</p>
      <h3>${item.title}</h3>
      <p>${item.detail}</p>
    </article>
  `).join("");
}

function renderCallHistory(callHistory) {
  const kpiContainer = document.getElementById("call-kpi-grid");
  const breakdownContainer = document.getElementById("call-breakdown-list");
  const scopeLabel = document.getElementById("call-scope-label");
  const summary = callHistory?.summary || [];
  const segments = callHistory?.segments || [];
  const scope = callHistory?.scope || "watchlist";

  if (scopeLabel) {
    scopeLabel.textContent = "Showing live call data for the displayed watchlist accounts.";
  }

  if (!summary.length) {
    renderEmptyState(kpiContainer, "No call history placeholder data is available.");
  } else {
    kpiContainer.innerHTML = summary.map((item) => `
      <article class="kpi-card ${toneClass(item.tone)}">
        <p class="kpi-card__label">${item.label}</p>
        <p class="kpi-card__value">${item.value}</p>
        <p class="kpi-card__delta">${item.delta || ""}</p>
      </article>
    `).join("");
  }

  if (!segments.length) {
    renderEmptyState(breakdownContainer, "No call pressure bands are available.");
    return;
  }

  breakdownContainer.innerHTML = segments.map((item) => `
    <article class="insight-card">
      <div class="insight-card__header">
        <p class="insight-card__label">${item.label}</p>
        <p class="insight-card__value">${item.value}%</p>
      </div>
      <p class="insight-card__detail">${item.detail}</p>
    </article>
  `).join("");
}

function renderPlantHealth(plantHealth) {
  const kpiContainer = document.getElementById("plant-kpi-grid");
  const tableContainer = document.getElementById("plant-table-body");
  const summary = plantHealth?.summary || [];
  const modems = plantHealth?.modems || [];

  if (!summary.length) {
    renderEmptyState(kpiContainer, "No plant or node placeholder data is available.");
  } else {
    kpiContainer.innerHTML = summary.map((item) => `
      <article class="kpi-card ${toneClass(item.tone)}">
        <p class="kpi-card__label">${item.label}</p>
        <p class="kpi-card__value">${item.value}</p>
        <p class="kpi-card__delta">${item.delta || ""}</p>
      </article>
    `).join("");
  }

  if (!modems.length) {
    tableContainer.innerHTML = tableEmptyRow("No modem health rows are available.", 15);
    return;
  }

  tableContainer.innerHTML = modems.map((item) => `
    <tr>
      <td>${item.geo}</td>
      <td>${item.customer_id}</td>
      <td><strong>${item.modem_mac || "-"}</strong></td>
      <td><span class="${healthBadgeClass(item.status)}">${item.status}</span></td>
      <td>${item.ip || "-"}</td>
      <td>${item.last_seen || "-"}</td>
      <td>${item.usint || "-"}</td>
      <td>${item.usrxlvl || "-"}</td>
      <td>${item.ustxpwr || "-"}</td>
      <td>${item.usrxsnr || "-"}</td>
      <td>${item.dsrxlvl || "-"}</td>
      <td>${item.dsrxsnr || "-"}</td>
      <td>${item.dsprefec || "-"}</td>
      <td>${item.dspostfec || "-"}</td>
      <td>${item.dsbw || "-"}</td>
      <td>${item.usbw || "-"}</td>
      <td>${item.fiber_node || item.cmts || "-"}</td>
    </tr>
  `).join("");
}

function renderGeoSummary(items) {
  const container = document.getElementById("geo-table-body");
  if (!container) {
    return;
  }
  if (!items.length) {
    container.innerHTML = tableEmptyRow("No market watchlist rows match the current filter.", 8);
    return;
  }

  container.innerHTML = items.map((item) => `
    <tr>
      <td>${item.geo}</td>
      <td>${item.flagged_accounts}</td>
      <td>${item.avg_risk}</td>
      <td>${item.high_risk_count}</td>
      <td>${item.contactable_count}</td>
      <td>${item.top_driver}</td>
      <td><span class="${riskBadgeClass(item.risk_tier)}">${item.risk_tier}</span></td>
      <td>${item.recommended_action}</td>
    </tr>
  `).join("");
}

function renderCustomers(items) {
  const container = document.getElementById("customer-table-body");
  if (!container) {
    return;
  }
  const sortedItems = getSortedCustomers(items);
  if (!sortedItems.length) {
    container.innerHTML = tableEmptyRow("No high-risk customer rows match the current filter.", 7);
    if (customerPaginationSummary) {
      customerPaginationSummary.textContent = "Showing 0-0 of 0 accounts · Page 0 of 0";
    }
    if (customerPrevButton) {
      customerPrevButton.disabled = true;
    }
    if (customerNextButton) {
      customerNextButton.disabled = true;
    }
    return;
  }

  const meta = state.snapshot?.meta || {};
  const totalItems = Number(meta.customer_total_records || sortedItems.length || 0);
  const totalPages = Math.max(Number(meta.customer_total_pages || 1), 1);
  const currentPage = Math.max(Number(meta.customer_page || (state.customerPage + 1)), 1);
  state.customerPage = currentPage - 1;
  const startIndex = Number(meta.customer_row_start || 0);
  const endIndex = Number(meta.customer_row_end || 0);

  if (customerPaginationSummary) {
    customerPaginationSummary.textContent = `Showing ${startIndex}-${endIndex} of ${totalItems} accounts · Page ${currentPage} of ${totalPages}`;
  }
  if (customerPrevButton) {
    customerPrevButton.disabled = currentPage <= 1;
  }
  if (customerNextButton) {
    customerNextButton.disabled = currentPage >= totalPages;
  }

  container.innerHTML = sortedItems.map((item) => `
    <tr>
      <td class="customer-col-cell customer-col-cell--id"><span class="customer-cell customer-cell--id">${escapeHtml(item.customer_id)}</span></td>
      <td class="customer-col-cell customer-col-cell--geo"><span class="customer-cell customer-cell--geo">${escapeHtml(item.geo)}</span></td>
      <td class="customer-col-cell customer-col-cell--phone"><span class="customer-cell customer-cell--phone">${escapeHtml(item.phone_number || "-")}</span></td>
      <td class="customer-col-cell customer-col-cell--risk"><span class="customer-risk ${customerRiskBadgeClass(item.churn_probability)}">${escapeHtml(formatChurnProbability(item.churn_probability))}</span></td>
      <td class="customer-col-cell customer-col-cell--drivers"><span class="customer-cell customer-cell--drivers">${escapeHtml(item.drivers)}${item.modem_mac ? `<br><small>MAC ${escapeHtml(item.modem_mac)} · ${escapeHtml(item.modem_status || "Unavailable")}</small>` : ""}${item.fiber_node ? `<br><small>Node ${escapeHtml(item.fiber_node)}${item.cmts ? ` · CMTS ${escapeHtml(item.cmts)}` : ""}</small>` : item.cmts ? `<br><small>CMTS ${escapeHtml(item.cmts)}</small>` : ""}</span></td>
      <td class="customer-col-cell customer-col-cell--event"><span class="customer-cell customer-cell--event">${escapeHtml(item.last_event)}${item.modem_last_seen ? `<br><small>Seen ${escapeHtml(item.modem_last_seen)}</small>` : ""}</span></td>
      <td class="customer-col-cell customer-col-cell--action"><span class="customer-cell customer-cell--action">${escapeHtml(item.next_action)}${item.modem_ip ? `<br><small>IP ${escapeHtml(item.modem_ip)}</small>` : ""}</span></td>
    </tr>
  `).join("");
}

function render(snapshot) {
  if (!snapshot) return;
  renderStatus(snapshot.meta || {});
  renderHeroHighlights(snapshot);
  renderSummaryStrip(snapshot);
  renderKpis(snapshot.kpis || []);
  renderSignalMix(snapshot.signal_mix || []);
  renderCallHistory(snapshot.call_history || {});
  renderPlaybooks(snapshot.playbooks || []);
  renderPlantHealth(snapshot.modem_health || {});
  renderGeoSummary(snapshot.geo_summary || []);
  renderCustomers(snapshot.high_risk_customers || []);
}

async function fetchSnapshot(options = {}) {
  const {
    showFilterLoading = true,
    allowCachedPage = true,
  } = options;

  const cacheKey = buildSnapshotCacheKey();
  if (allowCachedPage && state.pageCache.has(cacheKey)) {
    state.snapshot = state.pageCache.get(cacheKey);
    const responsePage = Math.max(Number(state.snapshot?.meta?.customer_page || (state.customerPage + 1)), 1);
    state.customerPage = responsePage - 1;
    render(state.snapshot);
    return;
  }

  if (state.isFetching) {
    return;
  }
  state.isFetching = true;
  const originalLabel = refreshButton?.textContent || "Apply filters";
  if (refreshButton && showFilterLoading) {
    refreshButton.disabled = true;
    refreshButton.textContent = "Updating...";
  }
  try {
    const params = new URLSearchParams({
      segment: state.segment,
      location: state.location,
      limit: String(clampLimit(state.limit)),
      customer_page: String(state.customerPage + 1),
      customer_page_size: String(state.customerPageSize),
      customer_sort: state.customerSort,
    });
    const response = await fetch(`${state.endpoint}?${params.toString()}`, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(`Dashboard request failed with status ${response.status}`);
    }
    state.snapshot = await response.json();
    state.pageCache.set(cacheKey, state.snapshot);
    const responsePage = Math.max(Number(state.snapshot?.meta?.customer_page || (state.customerPage + 1)), 1);
    state.customerPage = responsePage - 1;
    render(state.snapshot);
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("segment", state.segment);
    nextUrl.searchParams.set("limit", String(state.limit));
    nextUrl.searchParams.set("customer_page", String(state.customerPage + 1));
    nextUrl.searchParams.set("customer_page_size", String(state.customerPageSize));
    nextUrl.searchParams.set("customer_sort", state.customerSort);
    if (state.location) {
      nextUrl.searchParams.set("location", state.location);
    } else {
      nextUrl.searchParams.delete("location");
    }
    window.history.replaceState({}, "", nextUrl);
  } catch (error) {
    console.error(error);
    const statusMessage = document.getElementById("status-message");
    if (statusMessage) {
      statusMessage.textContent = error.message || "Dashboard refresh failed.";
    }
  } finally {
    state.isFetching = false;
    if (refreshButton && showFilterLoading) {
      refreshButton.disabled = false;
      refreshButton.textContent = originalLabel;
    }
  }
}

filtersForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  state.segment = (segmentInput?.value || "res").trim().toLowerCase() === "com" ? "com" : "res";
  state.location = (locationInput?.value || "").trim();
  state.limit = clampLimit(limitInput?.value || state.limit || 12);
  if (limitInput) {
    limitInput.value = String(state.limit);
  }
  clearSnapshotCache();
  fetchSnapshot();
});

refreshIntervalInput?.addEventListener("change", (event) => {
  const nextRefreshSeconds = Number(event.target.value || 0);
  setAutoRefreshInterval(nextRefreshSeconds);
});

customerPrevButton?.addEventListener("click", () => {
  if (state.customerPage === 0) {
    return;
  }
  state.customerPage -= 1;
  fetchSnapshot({ showFilterLoading: false, allowCachedPage: true });
});

customerNextButton?.addEventListener("click", () => {
  const totalPages = Math.max(Number(state.snapshot?.meta?.customer_total_pages || 1), 1);
  const lastPage = totalPages - 1;
  if (state.customerPage >= lastPage) {
    return;
  }
  state.customerPage += 1;
  fetchSnapshot({ showFilterLoading: false, allowCachedPage: true });
});

customerSortInput?.addEventListener("change", (event) => {
  state.customerSort = event.target.value || "desc";
  state.customerPage = 0;
  clearSnapshotCache();
  fetchSnapshot();
});

render(state.snapshot);
setAutoRefreshInterval(state.refreshSeconds);

(() => {
  "use strict";

  const POLL_INTERVAL_MS = 3000;
  const STALE_AFTER_MS = 7000;
  const REQUEST_TIMEOUT_MS = 8000;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const SESSION_TOKEN_KEY = "hypoloom-observer-token";

  const byId = (id) => document.getElementById(id);
  const connection = byId("connection");
  const connectionText = byId("connection-text");
  const screenStatus = byId("screen-status");
  const graph = byId("dependency-graph");
  const graphViewport = byId("graph-viewport");
  const graphEdges = byId("graph-edges");
  const graphNodes = byId("graph-nodes");
  const graphEmpty = byId("graph-empty");
  const topologyList = byId("topology-list");
  const drawer = byId("evidence-drawer");
  const drawerBackdrop = byId("drawer-backdrop");
  const relationTooltip = byId("relation-tooltip");
  const helpDialog = byId("help-dialog");
  const supportDialog = byId("support-dialog");
  const filterIds = ["filter-search", "filter-component", "filter-expert", "filter-kind", "filter-state"];

  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const fragmentToken = fragment.get("token");
  let accessToken = fragmentToken || "";
  let fragmentStored = false;
  if (fragmentToken) {
    try {
      window.sessionStorage.setItem(SESSION_TOKEN_KEY, fragmentToken);
      fragmentStored = window.sessionStorage.getItem(SESSION_TOKEN_KEY) === fragmentToken;
    } catch (_error) {
      fragmentStored = false;
    }
  } else {
    try {
      accessToken = window.sessionStorage.getItem(SESSION_TOKEN_KEY) || "";
    } catch (_error) {
      accessToken = "";
    }
  }
  const answerStorageKey = `${SESSION_TOKEN_KEY}.answers`;
  let answerToken = fragment.get("answer_token") || "";
  try {
    if (fragmentToken) {
      if (answerToken) window.sessionStorage.setItem(answerStorageKey, JSON.stringify({accessToken, answerToken}));
      else window.sessionStorage.removeItem(answerStorageKey);
    } else {
      const saved = JSON.parse(window.sessionStorage.getItem(answerStorageKey) || "{}");
      answerToken = saved.accessToken === accessToken ? saved.answerToken || "" : "";
    }
  } catch (_error) {
    if (answerToken) fragmentStored = false;
  }
  const providerStorageKey = `${SESSION_TOKEN_KEY}.provider`;
  let providerToken = fragment.get("provider_token") || "";
  try {
    if (fragmentToken) {
      if (providerToken) window.sessionStorage.setItem(providerStorageKey, JSON.stringify({accessToken, providerToken}));
      else window.sessionStorage.removeItem(providerStorageKey);
    } else {
      const saved = JSON.parse(window.sessionStorage.getItem(providerStorageKey) || "{}");
      providerToken = saved.accessToken === accessToken ? saved.providerToken || "" : "";
    }
  } catch (_error) { if (providerToken) fragmentStored = false; }
  // Clear a credential-bearing fragment only after the reload-safe session copy succeeds.
  if (fragmentToken && fragmentStored) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }

  const state = {
    snapshot: null,
    lastSuccessAt: 0,
    selectedKey: null,
    topology: [],
    topologyMode: "component",
    showPruning: true,
    activeView: "operations",
    graphBounds: null,
    view: { x: 36, y: 36, scale: 1 },
    dragging: null,
    fittedMode: null,
    product: null,
    selectedExperiment: null,
    logSocket: null,
    workbenchKind: "skills",
    reliabilityFilters: {role: "", node: "", model: ""},
    reliabilityMetric: "",
    workbenchOffset: 0,
    workbenchData: null,
    workbenchRequest: 0,
    workbenchContext: {},
    drawerMode: "evidence",
    detail: null,
    detailTab: "overview",
    detailRequest: 0,
    renderInputs: new Map(),
    workingTiles: new Map(),
    pollTimer: null,
    polling: false,
    pollController: null,
    pollFailures: 0,
    workbenchPending: null,
    history: {events: null, experiments: null},
    historyRequests: {events: 0, experiments: 0},
  };

  function element(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined && value !== null) item.textContent = String(value);
    return item;
  }

  function svgElement(tag, attributes = {}) {
    const item = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([name, value]) => item.setAttribute(name, String(value)));
    return item;
  }

  function setText(id, value) {
    byId(id).textContent = value === undefined || value === null || value === "" ? "—" : String(value);
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function object(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function sharedGoal(snapshot = state.snapshot) {
    return snapshot?.goal?.research_contract?.methodology?.controls?.product?.goal?.completion_mode === "shared_goal";
  }

  function renderObjective(snapshot) {
    const contract = object(object(snapshot.goal).research_contract);
    const objective = String(object(contract.goal).objective || object(snapshot.goal).objective || "Research objective unavailable");
    setText("mission-title", sharedGoal(snapshot) ? "Shared research goal" : "Research objective");
    setText("mission-summary", compact(objective.split(/\n/).find((line) => line.trim()) || objective, 220));
    const prose = byId("mission-objective");
    if (prose.dataset.objective === objective) return;
    prose.dataset.objective = objective;
    prose.replaceChildren(...objective.split(/\n\s*\n/).map((paragraph) => element("p", "", paragraph)));
  }

  function compact(value, limit = 72) {
    const rendered = value === undefined || value === null || value === "" ? "—" : String(value);
    return rendered.length > limit ? `${rendered.slice(0, limit - 1)}…` : rendered;
  }

  function fitNodeText(text, value, width, maxLines = 1) {
    // SVG glyph widths include the actual font and CJK fallback, unlike a
    // character limit. The element is attached before any measurements.
    let remaining = Array.from(String(value ?? "—").replace(/\s+/g, " ").trim());
    const lines = [];
    const fits = (value) => {
      text.textContent = value;
      return text.getComputedTextLength() <= width;
    };
    for (let line = 0; line < maxLines && remaining.length; line += 1) {
      if (fits(remaining.join(""))) { lines.push(remaining.join("")); break; }
      const suffix = line === maxLines - 1 ? "…" : "";
      let low = 0, high = remaining.length;
      while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        if (fits(remaining.slice(0, middle).join("") + suffix)) low = middle;
        else high = middle - 1;
      }
      // Prefer a word boundary when wrapping prose; unbroken IDs/CJK still fit.
      const space = remaining.slice(0, low).lastIndexOf(" ");
      if (!suffix && space > low / 2) low = space;
      lines.push(remaining.slice(0, low).join("").trimEnd() + suffix);
      remaining = remaining.slice(low);
      while (remaining[0] === " ") remaining.shift();
    }
    text.replaceChildren(...lines.map((line, index) => {
      const span = svgElement("tspan", {x: text.getAttribute("x"), dy: index ? 13 : 0});
      span.textContent = line;
      return span;
    }));
  }

  function hypothesisFailure(item) {
    if (item.kind !== "hypothesis") return "";
    const states = [item.raw.assessment, item.raw.state].map((value) => String(value || "").toUpperCase());
    if (states.includes("REFUTED")) return "Refuted";
    return states.includes("FAILED") ? "Failed" : "";
  }

  function graphStatusGlyph(failure) {
    return failure
      ? svgElement("path", {class: "status-glyph status-cross", d: "M 14 14 L 24 24 M 24 14 L 14 24"})
      : svgElement("rect", {class: "status-glyph", x: 14, y: 15, width: 9, height: 9, rx: 1});
  }

  function formatTimestamp(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "—";
    return new Date(value * 1000).toLocaleString([], {
      hour12: false,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function formatDuration(startedAt, endedAt) {
    if (typeof startedAt !== "number" || !Number.isFinite(startedAt)) return "—";
    const end = typeof endedAt === "number" && Number.isFinite(endedAt) ? endedAt : Date.now() / 1000;
    const seconds = Math.max(0, end - startedAt);
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  function announce(message) {
    screenStatus.textContent = message;
  }

  function setConnection(kind, message) {
    connection.dataset.state = kind;
    connectionText.textContent = message;
    providerButtons();
    if (kind !== "live" && state.snapshot) {
      setText("provider-title", "API status unconfirmed · observation stale");
    }
  }

  function statusClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "pruned") return "state-pruned";
    if (["pending", "ready", "created", "proposed", "originated", "inconclusive"].includes(normalized)) return "state-pending";
    if (["running", "open", "active", "executing", "evaluating"].includes(normalized)) return "state-running";
    if (["blocked", "failed", "cancelled", "canceled", "refuted", "integrity_rejected"].includes(normalized)) return "state-blocked";
    if (["accepted", "supported", "complete", "completed", "settled", "closed", "resolved", "terminal"].includes(normalized)) return "state-accepted";
    return "state-other";
  }

  function selectedFilters() {
    return {
      search: byId("filter-search").value.trim().toLowerCase(),
      component: byId("filter-component").value,
      expert: byId("filter-expert").value,
      kind: byId("filter-kind").value,
      state: byId("filter-state").value,
    };
  }

  function searchable(record) {
    try {
      return JSON.stringify(record).toLowerCase();
    } catch (_error) {
      return "";
    }
  }

  function attemptMatches(attempt, filters = selectedFilters()) {
    if (filters.search && !searchable(attempt).includes(filters.search)) return false;
    if (filters.component && (attempt.task_scope !== "expert" || attempt.component_id !== filters.component)) return false;
    if (filters.expert && (attempt.task_scope !== "expert" || attempt.owner_role !== filters.expert)) return false;
    if (filters.kind && attempt.kind !== filters.kind) return false;
    if (filters.state && attempt.state !== filters.state) return false;
    return true;
  }

  function relatedAttempts(componentId, role) {
    return list(state.snapshot && state.snapshot.attempts).filter((attempt) =>
      (!componentId || attempt.task_scope === "expert" && attempt.component_id === componentId) && (!role || attempt.task_scope === "expert" && attempt.owner_role === role));
  }

  function relatedRendezvous(componentId, role) {
    return list(state.snapshot && state.snapshot.rendezvous).filter((record) =>
      (!componentId || record.component_id === componentId) &&
      (!role || [record.requester_role, record.target_role, record.responder_role].includes(role)));
  }

  function componentMatches(component, filters = selectedFilters()) {
    if (filters.search && !searchable(component).includes(filters.search)) return false;
    if (filters.component && component.component_id !== filters.component) return false;
    if (filters.expert && component.owner_role !== filters.expert && !list(component.proposer_roles).includes(filters.expert)) return false;
    const attempts = relatedAttempts(component.component_id, "");
    const rendezvous = relatedRendezvous(component.component_id, "");
    if (filters.kind && !attempts.some((attempt) => attempt.kind === filters.kind) && !rendezvous.some((record) => record.kind === filters.kind)) return false;
    if (filters.state && component.state !== filters.state && !attempts.some((attempt) => attempt.state === filters.state) && !rendezvous.some((record) => record.status === filters.state)) return false;
    return true;
  }

  function expertMatches(expert, filters = selectedFilters()) {
    if (filters.search && !searchable(expert).includes(filters.search)) return false;
    if (filters.expert && expert.role !== filters.expert) return false;
    if (filters.component && !list(expert.owned_component_ids).includes(filters.component) && !list(expert.proposing_component_ids).includes(filters.component)) return false;
    const attempts = relatedAttempts("", expert.role);
    const rendezvous = relatedRendezvous("", expert.role);
    const components = list(state.snapshot && state.snapshot.components).filter(
      (component) => component.owner_role === expert.role || list(component.proposer_roles).includes(expert.role),
    );
    if (filters.kind && !attempts.some((attempt) => attempt.kind === filters.kind) && !rendezvous.some((record) => record.kind === filters.kind)) return false;
    if (filters.state && !attempts.some((attempt) => attempt.state === filters.state) && !rendezvous.some((record) => record.status === filters.state) && !components.some((component) => component.state === filters.state)) return false;
    return true;
  }

  function rendezvousMatches(record, filters = selectedFilters()) {
    if (filters.search && !searchable(record).includes(filters.search)) return false;
    if (filters.component && record.component_id !== filters.component) return false;
    if (filters.expert && ![record.requester_role, record.target_role, record.responder_role].includes(filters.expert)) return false;
    if (filters.kind && record.kind !== filters.kind) return false;
    if (filters.state && record.status !== filters.state) return false;
    return true;
  }

  function topologyMatches(item, filters = selectedFilters()) {
    if (filters.search && !searchable(item).includes(filters.search)) return false;
    if (filters.component && item.componentId !== filters.component) return false;
    const view = item.kind === "component" ? object(object(item.raw).view) : {};
    const role = item.kind === "hypothesis"
      ? object(item.raw).proposer_role
      : view.owner_role || object(object(item.raw).contract).owner;
    const proposerRoles = item.kind === "component" ? list(view.proposer_roles) : [];
    if (filters.expert && role !== filters.expert && !proposerRoles.includes(filters.expert)) return false;
    const attempts = relatedAttempts(item.componentId, "");
    const rendezvous = relatedRendezvous(item.componentId, "");
    if (filters.kind && !attempts.some((attempt) => attempt.kind === filters.kind) && !rendezvous.some((record) => record.kind === filters.kind)) return false;
    if (filters.state && item.state !== filters.state && !attempts.some((attempt) => attempt.state === filters.state) && !rendezvous.some((record) => record.status === filters.state)) return false;
    return true;
  }

  function updateSelect(id, values, emptyLabel) {
    const select = byId(id);
    const input = JSON.stringify([values, emptyLabel]);
    if (select.dataset.options === input) return;
    select.dataset.options = input;
    const previous = select.value;
    select.replaceChildren();
    const empty = element("option", "", emptyLabel);
    empty.value = "";
    select.append(empty);
    values.forEach((value) => {
      const option = element("option", "", value);
      option.value = value;
      select.append(option);
    });
    if (values.includes(previous)) select.value = previous;
  }

  function populateFilters(snapshot) {
    const shared = sharedGoal(snapshot);
    byId("filter-component-label").hidden = shared;
    byId("filter-component-label").parentElement.classList.toggle("shared-goal", shared);
    updateSelect("filter-component", shared ? [] : list(snapshot.components).map((item) => item.component_id).sort(), "All components");
    updateSelect("filter-expert", list(snapshot.experts).map((item) => item.role).sort(), "All experts");
    updateSelect(
      "filter-kind",
      Array.from(new Set([
        ...list(snapshot.attempts).map((item) => item.kind),
        ...list(snapshot.rendezvous).map((item) => item.kind),
      ])).sort(),
      "All kinds",
    );
    updateSelect(
      "filter-state",
      Array.from(new Set([
        ...buildTopology(snapshot, "component").map((item) => item.state),
        ...buildTopology(snapshot, "hypothesis").map((item) => item.state),
        ...list(snapshot.rendezvous).map((item) => item.status),
        ...list(snapshot.attempts).map((item) => item.state),
      ])).sort(),
      "All states",
    );
  }

  function expertClass(snapshot, role) {
    const expert = list(snapshot.experts).find((item) => item.role === role);
    const ordinal = Number(expert && expert.slot_ordinal);
    if (!Number.isInteger(ordinal) || ordinal < 1) return "expert-0";
    return `expert-${((ordinal - 1) % 7) + 1}`;
  }

  function appendDefinition(parent, name, value) {
    parent.append(element("dt", "", name), element("dd", "", value));
  }

  let resourcePending = null;
  let resourceBusy = false;
  let providerPending = null;
  let providerBusy = false;
  const dependencyPending = {};
  let dependencyBusy = false;
  function providerWritable() {
    return Boolean(providerToken && state.snapshot?.status?.provider_retry
      && connection.dataset.state === "live"
      && ["CREATED", "ACTIVE"].includes(state.snapshot.status.run_state)
      && Date.now() - state.lastSuccessAt < STALE_AFTER_MS);
  }
  function providerButtons() {
    byId("provider-controls").hidden = !providerToken;
    byId("provider-retry").disabled = providerBusy || !providerWritable();
    byId("provider-switch").disabled = providerBusy || !providerWritable() || Boolean(providerPending);
    byId("provider-settings").disabled = providerBusy || Boolean(providerPending);
    byId("provider-retry").textContent = providerPending ? "Retry same request" : "Try current API";
  }
  function renderProvider(snapshot) {
    const retry = snapshot.status?.provider_retry;
    const terminal = ["COMPLETED", "FAILED", "CANCELLED"].includes(snapshot.status?.run_state);
    const labels = {ready: "API dispatch available", cooldown: "API cooling down", waiting_operator: "API waiting for operator", recovering: "Trying API recovery"};
    setText("provider-title", terminal ? "Run ended" : retry ? labels[retry.state] || "API status" : "API status unknown");
    const reasons = {auth_failed: "Authentication rejected", rate_limited: "Rate limit; quota cause unconfirmed", quota_exhausted: "Quota exhausted", connection_configuration: "Connection configuration unavailable"};
    setText("provider-description", terminal ? "No API recovery is available for an ended run."
      : !retry ? "Live Host status is unavailable. API availability cannot be confirmed."
      : retry.state === "ready" ? "Normal dispatch is allowed, subject to task and budget limits."
      : retry.state === "recovering" ? "One pending task may try the API. Access is not yet confirmed."
      : `${reasons[retry.reason] || "API unavailable"}. New Agent dispatch paused; submitted experiments continue. ${retry.requires_operator ? "Operator action required." : `Next attempt: ${formatTimestamp(retry.next_retry_at)}.`} Quota reset time: unknown.`);
    setText("provider-access", providerToken
      ? "Operator recovery access includes GPU recheck, literature and notification retry. API switch uses a Host-side 0600 JSON connection file; the model stays fixed. Never enter a key here."
      : "Read-only. Use TUI p for API recovery or obtain an operator API link with --allow-provider-control.");
    const blocks = snapshot.status?.resource_blocked || [];
    setText("resource-blocked", blocks.length ? `GPU infrastructure blocked: ${blocks.map(b => `${b.role}: ${b.code}`).join(" · ")}. Submitted experiments continue; affected nodes await repair and recheck.` : "");
    if (terminal && blocks.length) setText("resource-blocked", "Run ended; GPU fault history is retained. No resource recovery is available.");
    const recheck = byId("resource-recheck");
    recheck.hidden = terminal || !providerToken || (!blocks.length && !resourcePending);
    recheck.disabled = resourceBusy || !providerWritable();
    recheck.textContent = resourcePending ? "Retry same resource recheck" : "Recheck GPU resources";
    const literature = snapshot.status?.literature_retry;
    setText("literature-status", !literature ? "Literature service status unknown."
      : `OpenAlex: ${literature.state === "ready" && !literature.recovered_at ? "retrieval allowed; access unverified" : literature.state.replaceAll("_", " ")}${literature.reason ? ` · ${literature.reason}` : ""}. ${literature.requires_operator ? "Operator action required." : literature.next_retry_at ? `Next attempt: ${formatTimestamp(literature.next_retry_at)}.` : ""} ${list(literature.affected_roles).join(", ")}`);
    const notifications = snapshot.status?.notifications;
    const sinks = Object.entries(notifications?.sinks || {}).filter(([name]) => name !== "log");
    setText("notification-status", !notifications || notifications.state === "unavailable" ? "Notification delivery status unknown; owning service unavailable."
      : notifications.error ? `Notification service: ${notifications.error}`
      : !sinks.length ? "No remote notification delivery has been observed."
      : sinks.map(([name, h]) => `${name}: ${(h.state || "unverified").replaceAll("_", " ")} · pending ${h.pending ?? "unknown"} · last success ${formatTimestamp(h.last_success_at)}${h.failure ? ` · ${h.failure.code} (${h.failure.phase}${h.failure.errno == null ? "" : `, errno ${h.failure.errno}`})` : ""}${h.next_retry_at ? ` · next retry ${formatTimestamp(h.next_retry_at)}` : ""}`).join(" | "));
    for (const kind of ["literature", "notification"]) {
      const button = byId(`${kind}-retry`);
      button.hidden = !providerToken || terminal || (kind === "notification" ? !notifications?.sinks?.telegram : !literature);
      button.disabled = dependencyBusy || !providerWritable();
      button.textContent = dependencyPending[kind] ? "Retry same request" : kind === "literature" ? "Retry literature service" : "Retry Telegram delivery";
    }
    providerButtons();
  }
  async function requestDependency(kind) {
    if (dependencyBusy || !providerWritable()) return;
    dependencyPending[kind] ||= {request_id: `web-${kind}-${crypto.randomUUID()}`};
    dependencyBusy = true; renderProvider(state.snapshot);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`/api/${kind}-retry`, {method: "POST", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${accessToken}`, "X-Hypoloom-Provider-Token": providerToken, "Content-Type": "application/json"}, body: JSON.stringify(dependencyPending[kind])});
      const result = await response.json();
      if (["accepted", "rejected", "conflict"].includes(result.status)) delete dependencyPending[kind];
      setText("dependency-operation", result.status === "accepted" ? "Retry requested. Recovery is confirmed only after an actual successful response." : "Retry not accepted; inspect current status.");
    } catch (_) { setText("dependency-operation", "Host reply not confirmed. Retry sends the same request."); }
    finally { clearTimeout(timer); dependencyBusy = false; renderProvider(state.snapshot); }
  }
  byId("literature-retry").addEventListener("click", () => requestDependency("literature"));
  byId("notification-retry").addEventListener("click", () => requestDependency("notification"));
  async function requestResources() {
    if (resourceBusy || !providerWritable()) return;
    resourcePending ||= {request_id: `web-resource-${crypto.randomUUID()}`};
    resourceBusy = true; byId("resource-recheck").disabled = true;
    setText("resource-operation", "Rechecking the bound GPU pool…");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch("/api/resource-recheck", {method: "POST", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${accessToken}`, "X-Hypoloom-Provider-Token": providerToken, "Content-Type": "application/json"}, body: JSON.stringify(resourcePending)});
      const result = await response.json();
      if (result.status === "accepted") {
        resourcePending = null; setText("resource-operation", "GPU pool rechecked. Existing retry and task budgets remain in effect.");
      } else if (["rejected", "conflict"].includes(result.status) || [400, 403].includes(response.status)) {
        resourcePending = null; setText("resource-operation", `Not accepted: ${result.error?.message || result.message || "check operator access"}`);
      } else throw new Error("unconfirmed");
    } catch (_error) { setText("resource-operation", "Host reply not confirmed. Retry sends the same resource recheck."); }
    finally { clearTimeout(timer); resourceBusy = false; renderProvider(state.snapshot); }
  }
  byId("resource-recheck").addEventListener("click", requestResources);
  async function requestProvider(switchConnection) {
    if (providerBusy || !providerWritable()) return;
    if (!providerPending) {
      const settings = byId("provider-settings").value.trim();
      if (switchConnection && !settings.startsWith("/")) {
        setText("provider-operation", "Select an absolute Host-side connection file path."); return;
      }
      providerPending = {request_id: `web-provider-${crypto.randomUUID()}`,
        ...(switchConnection ? {settings_path: settings} : {})};
    }
    providerBusy = true; providerButtons();
    setText("provider-operation", "Requesting API recovery…");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch("/api/provider-retry", {method: "POST", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${accessToken}`, "X-Hypoloom-Provider-Token": providerToken, "Content-Type": "application/json"},
        body: JSON.stringify(providerPending)});
      const result = await response.json();
      if (result.status === "accepted") {
        providerPending = null;
        setText("provider-operation", "Recovery requested. Awaiting a successful API response; existing results and budgets are preserved.");
      } else if (["rejected", "conflict"].includes(result.status) || [400, 403].includes(response.status)) {
        providerPending = null;
        setText("provider-operation", `Not accepted: ${result.error?.message || result.message || "check operator access and connection file"}`);
      } else throw new Error("unconfirmed");
    } catch (_error) {
      setText("provider-operation", "Host reply not confirmed. Retry sends the same request; inspect current state before another operation.");
    } finally { clearTimeout(timer); providerBusy = false; providerButtons(); }
  }
  byId("provider-retry").addEventListener("click", () => requestProvider(false));
  byId("provider-switch").addEventListener("click", () => requestProvider(true));

  function renderHud(snapshot) {
    renderProvider(snapshot);
    renderHumanDecisions(snapshot);
    const status = object(snapshot.status);
    const oracle = object(snapshot.oracle);
    setText("run-id", snapshot.run_id);
    const hostObservation = object(object(state.snapshot && state.snapshot.overview).host_observation);
    const active = !["COMPLETED", "CANCELLED", "FAILED"].includes(status.run_state);
    const hostAbsent = active && hostObservation.availability === "absent";
    setText("run-state", `${status.run_state}${hostAbsent ? " · HOST ABSENT (orphaned)" : ""}${active && status.dispatch_error ? " · DISPATCH PAUSED (Host error)" : ""}`);
    setText("goal-verdict", status.goal_verdict || "PENDING");
    setText(
      "required-assessed",
      `${status.required_assessment_count ?? "?"}/${status.required_component_count ?? "?"}`,
    );
    setText("run-revision", status.run_revision);
    setText("observed-at", formatTimestamp(snapshot.observed_at));
    const runOpen = status.open_attempts;
    const rootOpen = snapshot.durable_open_attempts_state_root;
    setText("open-attempts", `${runOpen ?? "?"} run / ${rootOpen ?? "?"} root`);
    const audit = object(oracle.observation);
    setText("oracle-state", audit.state === "pending" ? "AUDIT PENDING" : audit.state === "error" ? "AUDIT UNAVAILABLE"
      : oracle.ok === true ? "LAST CLEAN" : oracle.ok === false ? "LAST ISSUES" : "UNKNOWN");
    byId("oracle-state").title = audit.audited_at ? `Historical audit from ${formatTimestamp(audit.audited_at)}${audit.refreshing ? "; refresh pending" : ""}` : "No completed audit time available";
  }

  function renderMission(snapshot) {
    const status = object(snapshot.status);
    const goalPayload = object(snapshot.goal);
    const contract = object(goalPayload.research_contract);
    renderObjective(snapshot);
    const schema = contract.schema_version === undefined ? "?" : contract.schema_version;
    byId("mission-meta").textContent =
      `${snapshot.status_source || "offline projection"} · contract schema ${schema} · ` +
      (answerToken ? "operator answers require review and explicit submission" : "read-only observation; operator link required to submit answers");
    const dialogue = list(status.grounding_dialogue);
    byId("grounding-dialogue-panel").hidden = dialogue.length === 0;
    const history = byId("grounding-dialogue-list");
    history.replaceChildren();
    dialogue.forEach((item) => {
      const card = element("article", "panel");
      card.append(element("h3", "", `Round ${item.ordinal} · ${item.state}`),
        element("p", "", item.question), element("p", "", item.rationale),
        element("p", "", `Decision: ${item.decision_id}`));
      if (list(item.options).length) card.append(element("p", "", `Suggestions: ${item.options.join(" / ")}`));
      if (item.answer !== null) card.append(element("p", "", `Answer: ${item.answer}`));
      else card.append(element("p", "", item.state === "OPEN" ? "Waiting for your answer; no model invocation remains open." : `Closed: ${item.resolution}`));
      const review = element("button", "", item.state === "OPEN" ? "Review and answer" : "View decision");
      review.type = "button";
      review.addEventListener("click", () => openHuman(item.decision_id));
      card.append(review);
      history.append(card);
    });
    renderSharedAcceptance(snapshot);
  }

  function renderSharedAcceptance(snapshot) {
    let panel = byId("shared-acceptance-panel");
    if (!panel) {
      panel = element("section", "panel");
      panel.id = "shared-acceptance-panel";
      byId("grounding-dialogue-panel").after(panel);
    }
    const status = object(snapshot.status);
    const common = status.shared_acceptance;
    const expanded = typeof panel.querySelectorAll === "function"
      ? Array.from(panel.querySelectorAll("details")).map((details) => details.open) : [];
    panel.hidden = !common;
    panel.replaceChildren();
    if (!common) return;
    const acceptance = common.acceptance || {};
    const rawDetails = (title, value) => {
      const details = element("details", "acceptance-source");
      details.append(element("summary", "", title), element("pre", "", JSON.stringify(value, null, 2)));
      return details;
    };
    panel.append(element("h2", "", "Shared acceptance"),
      element("p", "prose", acceptance.summary || "Accepted criteria unavailable."),
      element("p", "", acceptance.rule === "ALL" ? "All conditions must hold (ALL)"
        : acceptance.rule === "ANY" ? "Any condition may hold (ANY)" : "Acceptance rule unavailable"),
      element("p", "", `Grounding source Attempt: ${common.source_attempt_id}`),
      element("p", "panel-copy", "Accepted declaration and published observations only. The recorded goal verdict remains authoritative; expert values are not merged or compared here."));
    const criteria = element("ul", "acceptance-conditions");
    const operators = {GT: ">", GE: "≥", LT: "<", LE: "≤", EQ: "=", IS_TRUE: "must be true"};
    list(acceptance.criteria).forEach((criterion) => criteria.append(element("li", "",
      `${criterion.name} ${operators[criterion.operator] || criterion.operator}${criterion.operator === "IS_TRUE" ? "" : ` ${criterion.target}`}`)));
    panel.append(criteria, element("h3", "", "Required validity checks"));
    const validity = element("ul", "acceptance-validity");
    list(acceptance.validity_checks).forEach((name) => validity.append(element("li", "", `${name} must be true`)));
    if (!list(acceptance.validity_checks).length) validity.append(element("li", "", "No separate validity checks declared."));
    panel.append(validity, rawDetails("Technical source · declaration and parser", acceptance));
    renderMetricTrends(panel, common, snapshot);
    const outcomes = list(common.outcomes);
    if (!outcomes.length) panel.append(element("p", "", "No published experiment outcomes yet."));
    const records = element("details", "acceptance-records");
    records.append(element("summary", "", `Published experiment records · ${outcomes.length}`));
    outcomes.forEach((item) => {
      const card = element("article", "panel");
      card.append(element("h3", "", `${item.experiment_id} · ${item.scope || "scope unavailable"}`),
        element("p", "", `Component: ${item.component_id} · candidate: ${item.method_family_id}`),
        element("p", "", `Outcome: ${item.outcome_id} · artifact: ${item.artifact_id}`),
        element("p", "", `Preregistered source Attempt: ${item.source_attempt_id}`),
        element("p", "", `Execution: ${item.execution_terminal_reason || "unknown"} · source Process only`),
        element("p", "", `Recorded outcome: ${item.local_outcome} · local metric: ${item.local_metric_value ?? "none"}`),
        element("p", "", `LOCAL criterion: ${!item.local_measurement ? "not declared" : item.local_outcome === "SUCCESS" ? "passed" : item.local_outcome === "CLEAN_REFUTATION" ? "not met" : `unavailable (${item.local_outcome || "unknown outcome"})`}. Shared GOAL verdict: ${status.goal_verdict || "PENDING"} (run-wide).`));
      if (item.goal_evaluation) {
        card.append(element("h4", "", "Published observations"));
        const observations = element("dl", "acceptance-observations");
        Object.entries(item.goal_evaluation.measurements || {}).forEach(([name, value]) => observations.append(element("dt", "", name), element("dd", "", value)));
        Object.entries(item.goal_evaluation.checks || {}).forEach(([name, value]) => observations.append(element("dt", "", name), element("dd", "", String(value))));
        card.append(observations, rawDetails("Technical source · recorded outcome", item.goal_evaluation));
      }
      else card.append(element("p", "", item.scope === "LOCAL"
        ? "LOCAL: not a common-goal evaluation."
        : "No accepted common observations published for this outcome."));
      records.append(card);
    });
    if (outcomes.length) panel.append(records);
    if (typeof panel.querySelectorAll === "function") panel.querySelectorAll("details").forEach((details, index) => { details.open = expanded[index] || false; });
  }

  const METRIC_OPERATORS = {GT: ">", GE: "≥", LT: "<", LE: "≤", EQ: "="};
  const hiddenMetricSeries = new Set();

  function metricNumber(raw) {
    if (!(typeof raw === "number" || typeof raw === "string" && raw.trim())) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  function formatMetric(value) {
    const absolute = Math.abs(value);
    if (absolute >= 1000 || (absolute > 0 && absolute < 0.001)) return value.toExponential(2);
    return String(Number(value.toPrecision(4)));
  }

  function hypothesisRoot(hypotheses, id) {
    let current = id;
    const seen = new Set([id]);
    let node = hypotheses.get(id);
    while (node && typeof node.parent_hypothesis_id === "string" && node.parent_hypothesis_id && !seen.has(node.parent_hypothesis_id)) {
      seen.add(node.parent_hypothesis_id);
      current = node.parent_hypothesis_id;
      node = hypotheses.get(current);
    }
    return current;
  }

  function hypothesisDepth(hypotheses, id) {
    let depth = 0;
    const seen = new Set([id]);
    let node = hypotheses.get(id);
    while (node && typeof node.parent_hypothesis_id === "string" && node.parent_hypothesis_id && !seen.has(node.parent_hypothesis_id)) {
      seen.add(node.parent_hypothesis_id);
      depth += 1;
      node = hypotheses.get(node.parent_hypothesis_id);
    }
    return depth;
  }

  function compareMetricPoints(left, right) {
    return left.depth - right.depth
      || String(left.outcome.experiment_id).localeCompare(String(right.outcome.experiment_id), undefined, {numeric: true})
      || String(left.outcome.outcome_id).localeCompare(String(right.outcome.outcome_id));
  }

  function metricTrendModel(common, snapshot) {
    const hypotheses = new Map(list(snapshot.hypotheses)
      .filter((item) => item && typeof item.hypothesis_id === "string" && item.hypothesis_id)
      .map((item) => [item.hypothesis_id, item]));
    const ownerOf = (componentId) => {
      const component = list(snapshot.components).find((item) => item && item.component_id === componentId);
      return component ? component.owner_role : null;
    };
    const series = new Map();
    const metrics = new Map();
    list(common.outcomes).forEach((outcome) => {
      const evaluation = outcome.goal_evaluation;
      if (!evaluation || typeof evaluation !== "object") return;
      const observed = Object.entries(object(evaluation.measurements))
        .map(([name, raw]) => ({name, value: metricNumber(raw), raw}))
        .filter((point) => point.value !== null);
      if (!observed.length) return;
      const hypothesisId = typeof outcome.hypothesis_id === "string" && outcome.hypothesis_id ? outcome.hypothesis_id : null;
      const rootId = hypothesisId ? hypothesisRoot(hypotheses, hypothesisId) : null;
      const root = rootId ? hypotheses.get(rootId) : null;
      const key = rootId || `component:${outcome.component_id || "unknown"}`;
      if (!series.has(key)) {
        series.set(key, {
          key,
          label: rootId || `${outcome.component_id || "unknown"} (no recorded hypothesis)`,
          title: root ? (root.claim || rootId)
            : "These outcomes are not linked to a recorded hypothesis; they are grouped by component.",
          accent: expertClass(snapshot, root ? root.proposer_role : ownerOf(outcome.component_id)),
        });
      }
      const base = {
        outcome,
        hypothesisId,
        depth: hypothesisId ? hypothesisDepth(hypotheses, hypothesisId) : 0,
      };
      observed.forEach(({name, value, raw}) => {
        if (!metrics.has(name)) metrics.set(name, new Map());
        const bucket = metrics.get(name);
        if (!bucket.has(key)) bucket.set(key, []);
        bucket.get(key).push({...base, value, raw});
      });
    });
    metrics.forEach((bucket) => bucket.forEach((points) => points.sort(compareMetricPoints)));
    const criteria = list(common.acceptance && common.acceptance.criteria);
    criteria.forEach((criterion) => {
      if (criterion && METRIC_OPERATORS[criterion.operator] && metricNumber(criterion.target) !== null
          && !metrics.has(criterion.name)) metrics.set(criterion.name, new Map());
    });
    const criteriaNames = criteria.map((criterion) => criterion && criterion.name)
      .filter((name) => typeof name === "string");
    const ordered = new Map();
    Array.from(metrics.keys()).sort((left, right) => {
      const a = criteriaNames.indexOf(left);
      const b = criteriaNames.indexOf(right);
      return (a === -1 ? criteriaNames.length : a) - (b === -1 ? criteriaNames.length : b) || left.localeCompare(right);
    }).forEach((name) => ordered.set(name, metrics.get(name)));
    const targets = new Map();
    criteria.forEach((criterion) => {
      if (!criterion || typeof criterion.name !== "string" || !METRIC_OPERATORS[criterion.operator]) return;
      const value = metricNumber(criterion.target);
      if (value !== null) targets.set(criterion.name, {operator: criterion.operator, value});
    });
    return {metrics: ordered, series, targets};
  }

  function localMetricTrendModel(common, snapshot) {
    const metrics = new Map(), series = new Map(), labels = new Map();
    list(common.outcomes).forEach((outcome) => {
      if (outcome.scope !== "LOCAL") return;
      const value = metricNumber(outcome.local_metric_value);
      const measurement = object(outcome.local_measurement);
      const metric = object(measurement.metric);
      if (value === null || !metric.name) return;
      // Local definitions and baselines may differ even within one lineage.
      // Group only one hypothesis and the same recorded declaration; never
      // compare local screening scores against the common GOAL thresholds.
      const hypothesisId = outcome.hypothesis_id || null;
      const key = JSON.stringify(["local", hypothesisId || outcome.experiment_id, measurement]);
      if (!metrics.has(key)) {
        const hypothesis = list(snapshot.hypotheses).find((item) => item.hypothesis_id === hypothesisId);
        const component = list(snapshot.components).find((item) => item.component_id === outcome.component_id);
        const role = hypothesis ? hypothesis.proposer_role : component && component.owner_role;
        labels.set(key, metric.name);
        series.set(key, {key, label: metric.name,
          context: [role, compact(hypothesis?.claim || hypothesisId || outcome.experiment_id, 64)].filter(Boolean).join(" · "),
          comparison: measurement.comparison === "DELTA_FROM_BASELINE" ? "Delta from baseline" : measurement.comparison === "RELATIVE_DELTA_FROM_BASELINE" ? "Relative delta from baseline" : measurement.comparison === "VALUE" ? "Value" : "Local measurement",
          criterion: `${metric.direction === 'MAXIMIZE' ? '≥' : metric.direction === 'MINIMIZE' ? '≤' : metric.direction} ${metric.target ?? '?'}`,

          title: [hypothesisId || outcome.experiment_id, hypothesis?.claim, measurement.comparison, measurement.parse_rule].filter(Boolean).join("\n"),
          accent: expertClass(snapshot, role)});
        metrics.set(key, new Map([[key, []]]));
      }
      metrics.get(key).get(key).push({outcome, hypothesisId, depth: 0, value, raw: outcome.local_metric_value});
    });
    metrics.forEach((bucket) => bucket.forEach((points) => points.sort(compareMetricPoints)));
    return {metrics, series, labels, targets: new Map()};
  }

  function renderMetricChart(metric, model) {
    const figure = element("figure", "metric-chart");
    figure.dataset.metric = metric;
    const label = model.labels ? model.labels.get(metric) : metric;
    const target = model.targets.get(metric);
    const width = 640;
    const height = 240;
    const margin = {left: 58, right: 14, top: 14, bottom: 30};
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const visible = [];
    model.metrics.get(metric).forEach((points, key) => {
      if (!hiddenMetricSeries.has(key)) visible.push({series: model.series.get(key), points});
    });
    const values = visible.flatMap((item) => item.points.map((point) => point.value));
    if (target) values.push(target.value);
    let low = values.length ? Math.min(...values) : 0;
    let high = values.length ? Math.max(...values) : 1;
    const pad = low === high ? Math.abs(low) * 0.05 || 0.05 : (high - low) * 0.08;
    low -= pad;
    high += pad;
    const y = (value) => margin.top + innerHeight - ((value - low) / (high - low)) * innerHeight;
    const maxLength = Math.max(1, ...visible.map((item) => item.points.length));
    const x = (index) => margin.left + (maxLength === 1 ? innerWidth / 2 : (index / (maxLength - 1)) * innerWidth);
    const svg = svgElement("svg", {viewBox: `0 0 ${width} ${height}`, class: "metric-svg", role: "img", "aria-label": `${label} trend`});
    for (let step = 0; step <= 3; step += 1) {
      const value = low + ((high - low) * step) / 3;
      const top = y(value);
      svg.append(svgElement("line", {class: "metric-grid", x1: margin.left, x2: width - margin.right, y1: top, y2: top}));
      const tick = svgElement("text", {class: "metric-tick", x: margin.left - 8, y: top + 3, "text-anchor": "end"});
      tick.textContent = formatMetric(value);
      svg.append(tick);
    }
    const xStep = Math.ceil(maxLength / 12);
    for (let index = 0; index < maxLength; index += 1) {
      if (index % xStep !== 0 && index !== maxLength - 1) continue;
      const tick = svgElement("text", {class: "metric-tick", x: x(index), y: height - 8, "text-anchor": "middle"});
      tick.textContent = String(index + 1);
      svg.append(tick);
    }
    if (target) {
      const top = y(target.value);
      svg.append(svgElement("line", {class: "metric-target-line", x1: margin.left, x2: width - margin.right, y1: top, y2: top}));
      const label = svgElement("text", {class: "metric-target-label", x: width - margin.right, y: top - 5, "text-anchor": "end"});
      label.textContent = `target ${METRIC_OPERATORS[target.operator]} ${formatMetric(target.value)}`;
      svg.append(label);
    }
    visible.forEach(({series, points}) => {
      const d = points.map((point, index) => `${index === 0 ? "M" : "L"} ${x(index).toFixed(1)} ${y(point.value).toFixed(1)}`).join(" ");
      svg.append(svgElement("path", {class: `metric-line ${series.accent}`, d}));
      points.forEach((point, index) => {
        const dot = svgElement("circle", {class: `metric-point ${series.accent}`, cx: x(index).toFixed(1), cy: y(point.value).toFixed(1), r: 3.4});
        const tooltip = svgElement("title");
        tooltip.textContent = `${point.outcome.experiment_id}\n${label} = ${point.raw}\n${point.hypothesisId ? `hypothesis ${point.hypothesisId}` : "no recorded hypothesis"}`;
        dot.append(tooltip);
        svg.append(dot);
      });
    });
    figure.append(element("figcaption", "", target ? `${label} · target ${METRIC_OPERATORS[target.operator]} ${formatMetric(target.value)}` : label), svg);
    return figure;
  }

  function renderLocalMetric(metric, model) {
    const points = model.metrics.get(metric).get(metric);
    const series = model.series.get(metric);
    const single = points.length === 1;
    const card = single ? element("article", `metric-single ${series.accent}`) : renderMetricChart(metric, model);
    card.dataset.metric = metric;
    if (single) {
      const value = element("strong", "metric-single-value", formatMetric(points[0].value));
      value.title = `Recorded value: ${points[0].raw}`;
      card.append(element("h5", "", series.label), value);
    }
    const context = element("p", "metric-local-context", series.context);
    context.title = series.title;
    const latest = points[points.length - 1].outcome;
    const localStatus = latest.local_outcome === "SUCCESS" ? "passed" : latest.local_outcome === "CLEAN_REFUTATION" ? "not met" : "unavailable";
    card.append(context, element("p", "panel-copy", `${points.length} published observation${single ? "" : "s"} · ${series.comparison}`),
      element("p", "metric-local-criterion", `Declared LOCAL criterion: ${series.criterion} · last recorded ${localStatus}`));
    return card;
  }

  function renderMetricTrends(panel, common, snapshot) {
    const model = metricTrendModel(common, snapshot);
    const section = element("section", "metric-trends");
    section.append(element("h3", "", "Metric trends"), element("h4", "", "GOAL evaluation"),
      element("p", "panel-copy", "Published observations connected per initial-hypothesis chain, in each chain's experiment order. Click a legend entry to hide or show that chain."));
    if (!model.series.size) section.append(element("p", "metric-empty panel-copy",
      list(common.outcomes).some((outcome) => outcome.goal_evaluation)
        ? "No numeric GOAL measurements are available. See published experiment records for checks and other results."
        : "Waiting for published GOAL measurements. Local screening and running experiments do not produce a final GOAL score."));
    const legend = element("div", "metric-legend");
    model.series.forEach((item) => {
      const hidden = hiddenMetricSeries.has(item.key);
      const chip = element("button", `metric-legend-chip ${item.accent}${hidden ? " off" : ""}`);
      chip.type = "button";
      chip.title = item.title;
      chip.setAttribute("aria-pressed", hidden ? "false" : "true");
      chip.append(element("span", "metric-swatch"), element("span", "metric-label", item.label));
      chip.addEventListener("click", () => {
        if (hiddenMetricSeries.has(item.key)) hiddenMetricSeries.delete(item.key);
        else hiddenMetricSeries.add(item.key);
        renderSharedAcceptance(snapshot);
      });
      legend.append(chip);
    });
    const grid = element("div", "metric-grid-charts");
    model.metrics.forEach((bucket, metric) => {
      if (bucket.size) grid.append(renderMetricChart(metric, model));
      else {
        const card = element("article", "metric-goal-empty");
        card.dataset.metric = metric;
        const target = model.targets.get(metric);
        card.append(element("h5", "", metric),
          element("p", "", `Target ${METRIC_OPERATORS[target.operator]} ${formatMetric(target.value)}`),
          element("p", "panel-copy", "No published measurement"));
        grid.append(card);
      }
    });
    section.append(legend, grid);
    const checkNames = Array.from(new Set([...list(common.acceptance?.validity_checks),
      ...list(common.acceptance?.criteria).filter((c) => c.operator === "IS_TRUE").map((c) => c.name)]));
    if (checkNames.length) {
      const checks = element("details", "metric-checks");
      checks.append(element("summary", "", "GOAL checks · per experiment"));
      const evaluated = list(common.outcomes).filter((item) => item.goal_evaluation);
      if (!evaluated.length) checks.append(element("p", "panel-copy", checkNames.map((name) => `${name}: unknown`).join(" · ")));
      evaluated.forEach((item) => {
        const observed = object(item.goal_evaluation.checks);
        checks.append(element("p", "panel-copy", `${item.experiment_id}: ${checkNames.map((name) =>
          `${name}: ${observed[name] === true ? 'true' : observed[name] === false ? 'false' : 'unknown'}`).join(' · ')}`));
      });
      section.append(checks);
    }
    const local = localMetricTrendModel(common, snapshot);
    const localCount = list(common.outcomes).filter((outcome) => outcome.scope === "LOCAL").length;
    if (localCount) {
      const localSection = element("section", "metric-local");
      localSection.append(element("h4", "", "LOCAL screening"), element("p", "panel-copy",
        "Single observations appear as results; multiple observations form a trend only within one hypothesis and the same measurement definition. Local scores are not GOAL scores and may use different baselines or scales."));
      const localGrid = element("div", "metric-grid-charts");
      local.metrics.forEach((bucket, metric) => localGrid.append(renderLocalMetric(metric, local)));
      if (!local.metrics.size) localSection.append(element("p", "metric-empty panel-copy", "Local records exist; numeric values or measurement definitions are unavailable."));
      localSection.append(localGrid);
      section.append(localSection);
    }
    panel.append(section);
  }

  function renderRuntime(snapshot) {
    const status = object(snapshot.status);
    const source = String(snapshot.status_source || "");
    const hasHostStatus = source.endsWith("+resident-host-status");
    setText("observer-boundary", providerToken
      ? `◉ OPERATOR API CONTROL${answerToken ? " / ANSWERS" : ""} · ${hasHostStatus ? "HOST STATUS ENRICHED" : "OFFLINE PROJECTION"}`
      : answerToken
      ? `◉ OPERATOR ANSWERS · ${hasHostStatus ? "HOST STATUS ENRICHED" : "OFFLINE PROJECTION"}`
      : hasHostStatus ? "◉ READ-ONLY · HOST STATUS ENRICHED" : "◉ READ-ONLY · OFFLINE PROJECTION");
    setText("runtime-source", hasHostStatus ? "HOST STATUS" : "OFFLINE");

    const authority = byId("authority-note");
    const authorityLabel = element("strong", "", "Authority boundary: ");
    const authorityText = hasHostStatus
      ? "The resident Host answered this poll, so quiescence, Agent usage, and resource waits below reflect that response. OPEN remains durable state, not process liveness; STATUS is not a continuous liveness signal. COMPLETED means lifecycle settlement, not GOAL_MET. Polls may straddle a commit, and Oracle CLEAN is not a scientific-success verdict."
      : "OPEN means durably open in the state store; it does not prove host, worker, or process liveness. Host quiescence, Agent usage, and live resource waits are unavailable offline. COMPLETED means lifecycle settlement, not GOAL_MET. Polls may straddle a commit, and Oracle CLEAN is not a scientific-success verdict.";
    authority.replaceChildren(authorityLabel, document.createTextNode(authorityText));

    if (!hasHostStatus) {
      setText("backup-quiescence", "Host STATUS unavailable");
      setText("agent-observed", "Host STATUS unavailable");
      setText("agent-budget", "Host STATUS unavailable");
    } else {
      const quiescent = status.quiescent_for_backup;
      setText("backup-quiescence", quiescent === true ? "true" : quiescent === false ? "false" : "unknown");
      const usage = object(status.agent_usage);
      const observed = object(usage.observed_lower_bound);
      setText(
        "agent-observed",
        `tokens ${observed.tokens ?? "?"} · turns ${observed.turns ?? "?"}`,
      );
      const hasCumulative = usage.profile_cumulative !== null &&
        typeof usage.profile_cumulative === "object" && !Array.isArray(usage.profile_cumulative);
      const cumulative = object(usage.profile_cumulative);
      const tokens = object(cumulative.tokens);
      const turns = object(cumulative.turns);
      const enforcement = object(usage.token_enforcement);
      const openDeclared = object(usage.open_declared_limits);
      const closedUnknown = object(usage.closed_unknown_declared_limits);
      const modes = Array.isArray(enforcement.modes) && enforcement.modes.length
        ? enforcement.modes.join("+")
        : "unknown";
      const tokenLimitLabel = modes === "observe" ? "reference" : "limit";
      const tokenLimit = !hasCumulative || !("limit" in tokens) ? "unknown" : tokens.limit ?? "unbounded";
      const turnLimit = !hasCumulative || !("limit" in turns) ? "unknown" : turns.limit ?? "unbounded";
      setText(
        "agent-budget",
        `admission total (actual + conservative bounds): ` +
          `tokens ${tokens.charged_or_reserved ?? "?"}/${tokenLimit} (${tokenLimitLabel}; ` +
          `aggregate overage +${tokens.overage ?? 0}) · policy ${modes} · ` +
          `per-call overages ${enforcement.observed_overage_attempts ?? "?"}` +
          `/+${enforcement.overage_tokens ?? "?"} tokens · ` +
          `turns ${turns.charged_or_reserved ?? "?"}/${turnLimit} · ` +
          `open reserve ${openDeclared.attempts ?? "?"} attempts / ` +
          `≤${openDeclared.tokens ?? "?"} tokens / ≤${openDeclared.turns ?? "?"} turns · ` +
          `closed accounting unknown ${usage.unobserved_closed_attempts ?? "?"} attempts / ` +
          `≤${closedUnknown.tokens ?? "?"} tokens / ≤${closedUnknown.turns ?? "?"} turns · ` +
          `local budget rejection ${usage.budget_rejected_attempts ?? "?"} attempts / zero charge`,
      );
    }

    const waitsContainer = byId("resource-wait-list");
    waitsContainer.replaceChildren();
    for (const process of status.process_activity || []) {
      const grant = process.resource_grant || {};
      const bytes = (process.logs || []).reduce((sum, row) => sum + row.bytes, 0);
      const ages = (process.logs || []).filter(row => row.bytes > 0).map(row => row.age_seconds);
      waitsContainer.append(element("p", "panel-copy", `${process.experiment_id}: ${grant.kind || "resource unknown"} ${(grant.gpu_uuids || []).join(", ")} · process ${process.process_alive === true ? "alive" : process.process_alive === false ? "exited, collection pending" : "liveness unknown"} · logs ${bytes} bytes${ages.length ? ` · last write ${Math.round(Math.min(...ages))} s ago` : " · no output observed"}. Scientific progress unknown; OPEN records ownership.`));
    }
    const waits = status.resource_waits;
    if (!hasHostStatus || !Array.isArray(waits)) {
      waitsContainer.append(element("p", "empty-copy", "Host STATUS unavailable; live GPU/capacity waits are unknown."));
    } else if (!waits.length) {
      waitsContainer.append(element("p", "empty-copy", "No resource wait reported by this Host poll."));
    } else {
      waits.forEach((value) => {
        const wait = object(value);
        const row = element("div", "resource-wait");
        row.append(
          element("strong", "", wait.experiment_id || "unnamed workflow"),
          element("span", "", wait.message || wait.reason || "unspecified resource wait"),
        );
        const planning = object(wait.resource_planning);
        const intent = object(planning.resource_intent);
        if (Number.isFinite(intent.workload_memory_mib)) {
          const budget = object(planning.budget);
          row.append(element("span", "", `GPU budget ${intent.workload_memory_mib} MiB + reserve ${intent.reserve_memory_mib} MiB` +
            (Number.isFinite(budget.sample_peak_mib) && Number.isFinite(budget.headroom_percent)
              ? ` · measured headroom ${budget.headroom_percent}% · ${intent.sharing}`
              : intent.sharing ? ` · ${intent.sharing} · no complete measurement` : "")));
        }
        waitsContainer.append(row);
      });
    }
  }

  const EXPERT_STAGES = [
    ["LITERATURE_REVIEW", "LIT", "Literature review"], ["HYPOTHESIS_PROPOSAL", "HYP", "Hypothesis proposal"],
    ["EXPERIMENT_DESIGN_IMPLEMENT", "DES", "Design / implementation"], ["EXPERIMENT_EXECUTION", "RUN", "Experiment execution"],
    ["RESULT_ANALYSIS", "ANA", "Result analysis"], ["HYPOTHESIS_UPDATE", "UPD", "Hypothesis update"],
    ["CYCLE_REPORT", "REP", "Cycle report"], ["ADJUDICATE", "JDG", "Adjudication"], ["SELF_EVOLVE", "EVO", "Self-evolution"],
    ["DEBATE", "DEB", "Debate round"], ["DEBATE_JUDGE", "JUD", "Debate judge"], ["LEADER", "LEAD", "Leader advice"],
  ];

  function compactNumber(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "?";
    let amount = value;
    for (const suffix of ["", "K", "M", "B", "T"]) {
      if (amount < 999.995 || suffix === "T") return `${Number(amount.toFixed(2))}${suffix}`;
      amount /= 1000;
    }
    return "?";
  }

  function compactDuration(seconds) {
    const value = Math.max(0, Math.round(seconds));
    if (value < 60) return `${value}s`;
    const minutes = Math.floor(value / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    return minutes % 60 ? `${hours}h${minutes % 60}m` : `${hours}h`;
  }

  function stageState(stage, host) {
    const status = stage.terminal_reason || stage.state || "UNKNOWN";
    return status === "OPEN" && host !== "responded" ? "unconfirmed" : status.toLowerCase();
  }

  function renderExpertActivity(parent, summary, snapshot) {
    const stages = list(summary.activity || summary.stages);
    const host = object(object(snapshot.overview).host_observation).availability ||
      (String(snapshot.status_source || "").endsWith("+resident-host-status") ? "responded" : "unconfirmed");
    const opened = stages.filter((stage) => stage.state === "OPEN");
    const exhausted = stages.some((stage) => stage.operational_status === "recovery_exhausted");
    const status = opened.length ? `${opened.length} OPEN · Host ${host}` : exhausted ? "Automatic recovery ended" : stages.length ? "Last recorded work" : "No recorded work";
    parent.append(element("p", `expert-work-status ${opened.length && host !== "responded" ? "stage-unconfirmed" : ""}`, `Cycle ${summary.cycle ?? "—"} · ${status}`));
    if (snapshot.status?.dispatch_error) parent.append(element("p", "blocked", "Dispatch paused · admitted work may still finish"));
    const lane = element("div", "expert-stage-lane");
    lane.setAttribute("aria-label", "Research stages; highlighted stages are current records, not completion percentage");
    EXPERT_STAGES.forEach(([node, short, name]) => {
      const matches = stages.filter((stage) => stage.node === node);
      const active = matches.filter((stage) => stage.state === "OPEN");
      const recent = active[0] || matches[matches.length - 1];
      const chip = element("span", `stage-chip stage-${recent ? stageState(recent, host) : "pending"}`, `${short}${active.length > 1 ? ` ×${active.length}` : ""}`);
      chip.title = `${name}: ${recent ? recent.terminal_reason || recent.state : "no current record"}`;
      chip.setAttribute("aria-label", chip.title);
      chip.dataset.stage = node;
      lane.append(chip);
    });
    parent.append(lane);
    const activity = element("div", "expert-current-work");
    stages.forEach((stage) => {
      const phase = stage.failure_stage === "literature_retrieval" ? "Literature preparation"
        : EXPERT_STAGES.find(([node]) => node === stage.node)?.[2] || stage.node || "Unknown stage";
      const scope = [stage.kind === "PROCESS" ? "process" : stage.subkind, stage.chain || stage.experiment_id].filter(Boolean).join(" · ");
      const status = stage.terminal_reason || stage.state || "UNKNOWN";
      const row = element("p", `expert-stage-detail stage-${stageState(stage, host)}`);
      row.append(element("strong", "", phase), document.createTextNode(` · ${scope ? `${scope} · ` : ""}${status} · ${stage.elapsed_seconds == null ? "elapsed ?" : formatDuration(0, stage.elapsed_seconds)}`));
      if (stage.operational_status === "recovery_exhausted") row.append(document.createTextNode(` · ${stage.attempts ?? "?"} attempts · automatic recovery ended`));
      if (stage.failure_code) row.append(document.createTextNode(` · ${stage.failure_code}`));
      else if (stage.operational_status && stage.operational_status !== "recovery_exhausted") row.append(document.createTextNode(` · ${stage.operational_status}`));
      if (stage.recovery_note) row.title = stage.recovery_note;
      activity.append(row);
    });
    if (!stages.length) activity.append(element("p", "panel-copy", "Current stage unavailable; open the expert history for retained evidence."));
    parent.append(activity);
  }

  function renderExpertParallelActivity(parent, summary) {
    const parallel = object(summary.parallel_activity);
    if (Object.keys(parallel).length) {
      const counts = element("div", "expert-parallel-work");
      counts.setAttribute("aria-label", "Hypotheses, proposal tasks and reading assistants");
      counts.append(element("p", "panel-copy", `Active research chains: ${summary.active_chains ?? "?"} / ${parallel.chain_limit ?? "?"}`));
      list(parallel.proposals).forEach((proposal) => {
        const admitted = `${proposal.accepted_candidates ?? "?"} accepted`;
        counts.append(element("p", "panel-copy", `${proposal.component_id} · cycle ${proposal.cycle} · hypotheses: ${admitted} / limit ${proposal.candidate_limit ?? "?"}`));
        if (!list(proposal.stages).length) return;
        const wings = element("p", "panel-copy proposal-wings");
        ["FORWARD", "BACKWARD", "SYNTHESIS"].forEach((name, index) => {
          const stage = list(proposal.stages).find((item) => item.subkind === name);
          const value = !stage ? "not dispatched" : stage.terminal_reason === "SUCCEEDED" ? "accepted"
            : stage.operational_status === "recovery_exhausted" ? "failed · recovery ended" : stage.terminal_reason || stage.state;
          if (index) wings.append(document.createTextNode(" · "));
          wings.append(element("span", "", `${name}: ${value}${stage?.attempts > 1 ? ` (${stage.attempts} attempts)` : ""}`));
        });
        counts.append(wings);
      });
      const readers = object(parallel.readers), statuses = object(readers.states), calls = object(readers.calls);
      counts.append(element("p", "panel-copy", `Reading assistants: ${readers.tasks ?? "?"} tasks · ${readers.attempts ?? "?"} attempts · queued ${statuses.queued ?? "?"} · running ${statuses.running ?? "?"} · completed ${statuses.completed ?? "?"} · failed ${statuses.failed ?? "?"} · cancelled ${statuses.cancelled ?? "?"}${statuses.unknown ? ` · unknown ${statuses.unknown}` : ""}`));
      const prefix = calls.unknown_attempts ? "≥" : "";
      counts.append(element("p", "panel-copy reading-tool-observation", `Reading tool: ${prefix}${calls.calls ?? "?"} calls · ${prefix}${calls.rejected ?? "?"} rejected${calls.unknown_attempts ? ` · ${calls.unknown_attempts} attempt traces incomplete/unavailable` : ""}${calls.unanswered ? ` · ${calls.unanswered} results unobserved` : ""}`));
      list(calls.observed_wait_reasons).forEach((reason) => counts.append(element("p", "panel-copy", `Recorded reading wait: ${reason} · may have changed`)));
      parent.append(counts);
    } else parent.append(element("p", "panel-copy", "Proposal and reading activity is unavailable in the current observation."));
  }

  function renderStageTokens(parent, values) {
    const table = element("table", "stage-token-table");
    const heading = element("tr", "");
    ["Stage", "Reported", "Failed calls", "Recovery calls", "Open observed", "Reserved"].forEach((name) => heading.append(element("th", "", name)));
    table.append(heading);
    list(values).forEach((stage) => {
      const row = element("tr", "");
      const usage = object(stage.accounting), recovery = object(stage.context_recovery);
      [stage.subkind ? `${stage.node} / ${stage.subkind}` : stage.node,
        usage.reported_tokens, usage.failed_reported_tokens, recovery.recovery_reported_tokens,
        usage.observed_unsettled_tokens, usage.reserved_tokens].forEach((value, index) =>
          row.append(element("td", "", index ? (Number.isInteger(value) ? compactNumber(value) : "unknown") : value)));
      row.title = `Usage lower bounds; unknown closed calls: ${usage.unknown_closed_attempts ?? '?'}; unavailable token records: ${usage.unavailable_token_attempts ?? '?'}`;
      table.append(row);
    });
    if (list(values).length) parent.append(table);
    else parent.append(element("p", "panel-copy", "Stage token accounting unavailable."));
    parent.append(element("p", "panel-copy", "Failed and recovery calls are subsets of reported usage and may overlap. Open observations and reserves are separate; reserves are not consumption. Missing usage is unknown."));
  }

  function renderExpertDetails(parent, role) {
    const snapshot = state.snapshot || {};
    const summary = object(list(object(snapshot.overview || state.product?.summary).experts)
      .find((item) => item.role === role));
    parent.append(element("h3", "", "Current work"));
    renderExpertActivity(parent, summary, snapshot);
    list(summary.activity).filter((stage) => stage.operational_status === "recovery_exhausted").forEach((stage) => {
      parent.append(element("p", "panel-copy", stage.recovery_note || "Automatic recovery ended for this node."));
      parent.append(actionButton("Failure details", () => openRemoteDetail("attempt", {attempt_id: stage.attempt_id}), "detail-link"));
    });
    parent.append(element("h3", "", "Tokens by stage"));
    renderStageTokens(parent, summary.stage_accounting);
    parent.append(element("h3", "", "Research chains, proposals and reading"));
    renderExpertParallelActivity(parent, summary);
    parent.append(actionButton("Browse expert history", () => {
      byId("filter-expert").value = role;
      closeDrawer();
      renderFilteredViews();
      openWorkbench("sessions");
    }));
  }

  function taskScopeLabel(record) {
    return record.task_scope === "run" ? "Global" : record.task_scope === "expert"
      ? `Expert · ${record.owner_role || "unavailable"}` : "Ownership unknown";
  }

  function renderGlobalTasks(snapshot) {
    const container = byId("global-task-list");
    container.replaceChildren();
    const overview = object(snapshot.overview);
    [["run", "global_tasks", "Global"], ["unknown", "unknown_tasks", "Ownership unknown"]].forEach(([scope, key, title]) => {
      const group = object(overview[key]);
      if (!list(group.stages).length) return;
      const row = element("article", "global-task-summary");
      const opened = list(group.activity).filter((item) => item.state === "OPEN");
      const stages = opened.length ? opened : list(group.stages).slice(-1);
      stages.forEach((stage) => {
        const button = actionButton(`${stage.node || "Task"} · ${stage.state === "OPEN" ? "OPEN" : stage.terminal_reason || stage.state} · ${compactDuration(stage.elapsed_seconds)}`,
          () => openRemoteDetail("attempt", {attempt_id: stage.attempt_id}), "detail-link global-task-attempt");
        button.title = `${title} task · OPEN is durable state, not process liveness`;
        row.append(button);
      });
      const usage = object(group.accounting);
      row.append(element("span", "panel-copy", ` ${title} · ≥${compactNumber(usage.reported_tokens)} tokens · reserved ${compactNumber(usage.reserved_tokens)} `));
      row.append(actionButton("Details & history", () => openLocalDetail("global_tasks", {task_scope: scope, name: `${title} tasks`}), "detail-link"));
      container.append(row);
    });
    const sharing = object(overview.grounding_sharing);
    if (sharing.state === "accepted") container.append(element("p", "panel-copy grounding-sharing",
      `Grounding accepted · supplied to ${list(sharing.experts).filter((item) => item.provided).length}/${list(sharing.experts).length} experts' task inputs`));
    else if (sharing.state === "waiting_human") container.append(element("p", "panel-copy", "Grounding awaiting clarification · review the questions above."));
    if (!container.children.length) container.append(element("p", "empty-copy", "No global task evidence yet."));
  }

  function renderGlobalDetails(parent, scope) {
    const overview = object(state.snapshot?.overview);
    const group = object(overview[scope === "run" ? "global_tasks" : "unknown_tasks"]);
    if (scope === "run") {
      const sharing = object(overview.grounding_sharing);
      parent.append(element("h3", "", "Grounding sharing"), element("p", "panel-copy",
        sharing.state === "accepted" ? "Accepted result available to subsequent tasks. Input delivery does not establish reading or understanding."
          : sharing.state === "waiting_human" ? "Awaiting clarification; no final accepted result."
          : "No final accepted Grounding result available."));
      if (sharing.source_attempt_id) parent.append(actionButton("Grounding source", () => openRemoteDetail("attempt", {attempt_id: sharing.source_attempt_id}), "detail-link"));
      list(sharing.experts).forEach((expert) => parent.append(element("p", "panel-copy",
        `${expert.role} · ${expert.provided ? "provided in task inputs" : "delivery not yet observed"}`)));
    }
    parent.append(element("h3", "", "Usage & recovery"));
    structuredDetail(parent, {tokens: group.accounting, context_recovery: Object.fromEntries(
      Object.entries(object(group.context_recovery)).filter(([key]) => !/cost|pricing|usd/i.test(key)))});
    compactReliability(parent, object(group.reliability).metrics, {expanded: true, taskScope: scope});
    parent.append(element("h3", "", "Recorded tasks"));
    list(group.stages).slice(-20).forEach((stage) => parent.append(actionButton(`${stage.node} · ${stage.terminal_reason || stage.state} · execution role ${stage.role || "unknown"}`,
      () => openRemoteDetail("attempt", {attempt_id: stage.attempt_id}), "detail-link")));
    parent.append(actionButton("Browse task history", () => {
      closeDrawer(); openWorkbench("sessions", {task_scope: scope});
    }));
  }

  function workingTilePhase(attemptId) {
    let hash = 0;
    for (const ch of String(attemptId)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    return -(hash % 24) / 10;
  }

  function workingTile(attempt, role) {
    let entry = state.workingTiles.get(attempt.attempt_id);
    if (entry) return entry;
    const light = element("span", "agent-tile-light");
    light.style.animationDelay = `${workingTilePhase(attempt.attempt_id)}s`;
    const fill = element("span", "agent-tile-fill");
    const value = element("span", "agent-tile-value");
    const bar = element("span", "agent-tile-bar");
    bar.append(fill, value);
    const tile = element("button", "agent-tile");
    tile.type = "button";
    tile.dataset.attempt = attempt.attempt_id;
    tile.append(light, bar);
    tile.addEventListener("click", () => {
      byId("filter-expert").value = role;
      renderFilteredViews();
      openWorkbench("sessions");
    });
    tile.classList.add("tile-enter");
    window.setTimeout(() => tile.classList.remove("tile-enter"), 320);
    entry = {tile, fill, value, role, leaving: false};
    state.workingTiles.set(attempt.attempt_id, entry);
    return entry;
  }

  function updateWorkingTile(entry, attempt, label, hostResponded, observedAt) {
    const {tile, fill, value} = entry;
    tile.classList.toggle("host-unconfirmed", !hostResponded);
    const elapsed = Math.max(0, observedAt - (Number(attempt.started_at) || observedAt));
    const hasWall = Number.isFinite(attempt.wall_seconds) && attempt.wall_seconds > 0;
    const overdue = hasWall && elapsed > attempt.wall_seconds;
    tile.classList.toggle("overdue", overdue);
    tile.classList.toggle("debate", attempt.command === "DEBATE" || attempt.command === "DEBATE_JUDGE");
    tile.classList.toggle("leader", attempt.command === "LEADER");
    const isProcess = attempt.kind === "PROCESS";
    const context = attempt.context || {};
    const separatedContext = Object.prototype.hasOwnProperty.call(context, "current_request_exchange");
    const known = !isProcess && Number.isInteger(attempt.context_tokens) && attempt.context_tokens >= 0;
    const priorKnown = separatedContext && Number.isInteger(context.last_observed_input_tokens) && context.last_observed_input_tokens >= 0;
    const limit = Number.isInteger(attempt.context_window_tokens) && attempt.context_window_tokens > 0 ? attempt.context_window_tokens : null;
    const valueText = isProcess ? compactDuration(elapsed) : known ? compactNumber(attempt.context_tokens)
      : priorKnown ? `last ${compactNumber(context.last_observed_input_tokens)}` : "—";
    value.textContent = attempt.operational_status === "compacting" ? `↻ ${valueText}` : valueText;
    fill.style.width = known && limit ? `${Math.min(100, Math.round((attempt.context_tokens / limit) * 1000) / 10)}%` : "0%";
    const lines = [`${label} · attempt ${attempt.attempt_id}`];
    if (isProcess) {
      lines.push(`elapsed ${compactDuration(elapsed)} · OPEN is durable state, not process liveness`);
    } else {
      if (separatedContext) {
        const currentExchange = context.current_request_exchange ?? "unknown";
        lines.push(`Current request: exchange ${currentExchange} · ${known ? `${compactNumber(attempt.context_tokens)} input tokens · ${attempt.context_source || "unknown source"}` : "no provider input usage"}`);
        lines.push(priorKnown ? `Last measured input: ${compactNumber(context.last_observed_input_tokens)} tokens · exchange ${context.last_observed_exchange ?? "unknown"}${context.last_observed_exchange !== context.current_request_exchange ? " (earlier request)" : ""}` : "Last measured input: unavailable");
        const estimate = context.current_request_estimated_input_tokens;
        const estimateSources = {provider_count_tokens: "provider token count", provider_usage_plus_delta_estimate: "prior measured usage plus estimated added input", utf8_bytes_conservative_estimate: "conservative UTF-8 byte estimate"};
        lines.push(`Current request estimate: ${Number.isInteger(estimate) ? compactNumber(estimate) : "unknown"} · ${estimateSources[context.current_request_estimate_source] || context.current_request_estimate_source || "unknown source"}`);
        if (typeof context.current_request_fits === "boolean") lines.push(`Current request capacity check: ${context.current_request_fits ? "within" : "exceeds"} input allowance`);
        if (context.current_request_estimate_reason) lines.push(`Estimate basis: ${context.current_request_estimate_reason}`);
      } else {
        lines.push(!known ? "no context reading yet"
          : `last input ${compactNumber(attempt.context_tokens)}${limit ? ` / ${compactNumber(limit)} working context limit` : " · context window unknown"} · ${attempt.context_source || "unknown source"}`);
        if (Number.isInteger(context.estimated_input_tokens)) lines.push(`Estimated input: ${compactNumber(context.estimated_input_tokens)} (conservative UTF-8 byte estimate, separate from provider usage)`);
      }
      if (attempt.context_window_source === "working_limit_only") lines.push("Provider window unconfirmed; limit is the framework working estimate.");
      if (Number.isInteger(attempt.cumulative_tokens)) lines.push(`Cumulative invocation usage: ${compactNumber(attempt.cumulative_tokens)} tokens`);
      if (Number.isInteger(context.available_input_tokens)) lines.push(`Admission input allowance: ${compactNumber(context.available_input_tokens)} · output reserve ${context.output_reserve_tokens ?? "?"} · tools ${context.tool_reserve_tokens ?? "?"} · margin ${context.estimate_margin_tokens ?? "?"}`);
      if (Number.isInteger(context.declared_context_tokens)) lines.push(`Configured provider window: ${compactNumber(context.declared_context_tokens)} tokens`);
      if (Number.isInteger(context.cache_read_input_tokens)) lines.push(`Cache read: ${compactNumber(context.cache_read_input_tokens)} input tokens`);
      if (Number.isInteger(context.cache_creation_input_tokens)) lines.push(`Cache creation: ${compactNumber(context.cache_creation_input_tokens)} input tokens`);
      if (attempt.session_scope) lines.push(`Session scope: ${attempt.session_scope}`);
      const observation = attempt.context_observation || {};
      if (Number.isInteger(observation.native_compaction_count)) lines.push(`Native compactions completed: ${observation.native_compaction_count} · explicit starts ${observation.native_compaction_started_count ?? "unknown"} · completion unobserved ${observation.native_compaction_completion_unobserved ?? "unknown"} · observed duration ${observation.native_compaction_duration_ms} ms · unknown durations ${observation.native_compaction_duration_unknown} · transcript coverage ${observation.coverage} · start-event coverage ${observation.native_compaction_start_coverage ?? "unavailable"}`);
      const compact = observation.last_native_compaction;
      if (compact) lines.push(`Last native compaction: ${compact.pre_tokens ?? "?"} → ${compact.post_tokens ?? "?"} input tokens · ${compact.duration_ms ?? "?"} ms`);
      if (Number.isInteger(observation.read_calls)) lines.push(`Successful Read calls: ${observation.read_calls} · exact frozen-input rereads ${observation.exact_reread_calls} (shell reads excluded)`);
      if (attempt.operational_status === "compacting") lines.push("Native CLI last reported: compacting");
      lines.push(`elapsed ${compactDuration(elapsed)}${hasWall ? ` of ${compactDuration(attempt.wall_seconds)} wall budget${overdue ? " · exceeded" : ""}` : ""} · OPEN is durable state, not process liveness`);
    }
    tile.title = lines.join("\n");
    tile.setAttribute("aria-label", `${label}: ${valueText}`);
  }

  function pauseNotice(pause, inDebate) {
    const reason = typeof pause.reason === "string" ? pause.reason : "";
    const scope = [pause.scope?.component_id, pause.scope?.chain].filter(Boolean).join(" / ");
    const debatePause = reason.startsWith("debate:");
    const active = debatePause && inDebate;
    const notice = element("div", `pause-notice${active ? " in-debate" : debatePause ? "" : " neutral"}`);
    const title = element("p", "pause-title");
    title.append(element("span", "pause-glyph", active ? "⚔" : "⏸"),
      document.createTextNode(active ? "Debate in progress · this expert is a debater"
        : debatePause ? "Debate proposed · dispatch paused" : "Dispatch paused"));
    notice.append(title, element("p", "pause-scope", `${scope ? `${scope} · ` : ""}admitted work may finish`));
    if (active) notice.append(actionButton("Leader & Debate →", () => openWorkbench("interactions"), "detail-link pause-link"));
    notice.title = [
      `Pause reason: ${reason || "unspecified"}`,
      scope ? `Scope: ${scope}` : "Scope: all admitted work of this expert",
      "Pause blocks new design / experiment / literature dispatch only; OPEN remains durable state, not process liveness.",
    ].join("\n");
    return notice;
  }

  function renderExperts(snapshot) {
    const experts = list(snapshot.experts).filter((expert) => expertMatches(expert));
    const container = byId("expert-list");
    const previousTiles = new Set();
    container.querySelectorAll(".agent-tile[data-attempt]").forEach((tile) => previousTiles.add(tile.dataset.attempt));
    container.replaceChildren();
    const seenTiles = new Set();
    const tileLists = new Map();
    byId("experts-panel").classList.toggle("shared-goal-experts", sharedGoal(snapshot));
    setText("expert-count", experts.length);
    if (!experts.length) {
      container.append(element("p", "empty-copy", "No expert projection."));
      return;
    }
    const summaries = list(object(snapshot.overview || object(state.product).summary).experts);
    experts.forEach((expert) => {
      const summary = object(summaries.find((item) => item.role === expert.role));
      const card = element("article", `entity-card expert-preview expert-accent ${expertClass(snapshot, expert.role)}`);
      card.dataset.expertRole = expert.role;
      const identity = element("div", "expert-identity");
      const openAttempts = list(snapshot.attempts).filter((attempt) => attempt && attempt.task_scope === "expert" && attempt.owner_role === expert.role && attempt.state === "OPEN").length;
      const heading = element("h3", "", expert.role || "unnamed role");
      if (openAttempts) {
        const confirmed = object(snapshot.overview).host_observation?.availability === "responded";
        const breath = element("span", `breath-dot${confirmed ? " host-confirmed" : " host-unconfirmed"}`);
        breath.title = `${openAttempts} durably open attempt${openAttempts === 1 ? "" : "s"} · ${confirmed ? "Host responded this poll" : "Host contact unconfirmed"} · OPEN is durable state, not process liveness`;
        breath.setAttribute("aria-label", confirmed ? "Open work; Host responded" : "Open work; activity unknown");
        heading.prepend(breath);
      }
      identity.append(element("p", "eyebrow", `EXPERT ${String(expert.slot_ordinal ?? "?").padStart(2, "0")}`), heading);
      const working = list(snapshot.attempts).filter((attempt) => attempt && attempt.task_scope === "expert" && attempt.owner_role === expert.role && attempt.state === "OPEN"
        && (attempt.kind === "AGENT" || attempt.kind === "PROCESS"));
      if (object(summary.leader).active > 0) {
        const crown = element("span", "leader-crown", "♛");
        crown.title = "Active Leader coordination · open details for advice or retained round semantics";
        crown.setAttribute("aria-label", "Active Leader");
        heading.append(crown);
      }
      if (working.length) {
        const stageByCommand = new Map(EXPERT_STAGES.map(([command, short, label]) => [command, label]));
        const observedAt = Number(snapshot.observed_at) || Date.now() / 1000;
        const hostResponded = object(snapshot.overview).host_observation?.availability === "responded";
        const sorted = [...working].sort((a, b) => (Number(a.started_at) - Number(b.started_at))
          || String(a.attempt_id).localeCompare(String(b.attempt_id)));
        const wrap = element("div", "working-agents");
        wrap.append(element("p", "subagent-title", `Working · ${working.length}`));
        const tiles = element("div", "agent-tile-list");
        sorted.slice(0, 6).forEach((attempt) => {
          const entry = workingTile(attempt, expert.role);
          seenTiles.add(attempt.attempt_id);
          const label = attempt.kind === "PROCESS" ? "Experiment process"
            : stageByCommand.get(attempt.command) || attempt.command || "unknown";
          updateWorkingTile(entry, attempt, label, hostResponded, observedAt);
          tiles.append(entry.tile);
        });
        if (working.length > 6) tiles.append(element("span", "agent-tile more", `+${working.length - 6} more`));
        wrap.append(tiles);
        identity.append(wrap);
        tileLists.set(expert.role, tiles);
      }
      if (sharedGoal(snapshot)) {
        list(snapshot.components).filter((component) => component.owner_role === expert.role).forEach((component) => {
          identity.append(element("p", "expert-progress", `Progress: ${component.phase || "not started"} · ${component.state || "unavailable"}`));
          if (component.blocked) identity.append(element("p", "blocked", `Blocked: ${list(component.blocked_reasons).join("; ") || "unspecified"}`));
          if (component.revision_warning) identity.append(element("p", "blocked", `Repeated revisions without convergence; cause unverified · ${component.cycle_revisions ?? 0} revisions in cycle ${component.current_cycle || "—"}`));
        });
      } else {
        identity.append(element("p", "", `owns: ${list(expert.owned_component_ids).join(", ") || "—"}`));
        identity.append(element("p", "", `proposes: ${list(expert.proposing_component_ids).join(", ") || "—"}`));
      }
      list(expert.pause_requested).forEach((pause) => {
        identity.append(pauseNotice(pause, working.some((attempt) => attempt.command === "DEBATE" || attempt.command === "DEBATE_JUDGE")));
      });
      const inspect = actionButton("Expert details", () =>
        openLocalDetail("expert", {role: expert.role, name: `Expert · ${expert.role}`}));
      inspect.dataset.expertDetails = expert.role;
      identity.append(inspect);
      const work = element("div", "expert-work");
      renderExpertActivity(work, summary, snapshot);
      const preview = element("div", "reliability-preview");
      preview.setAttribute("aria-label", `${expert.role} cumulative execution reliability`);
      compactReliability(preview, object(summary.reliability).metrics, {role: expert.role});
      work.append(preview);
      const accounting = object(summary.accounting);
      if (accounting.observed_unsettled_tokens > 0) {
        work.append(element("p", "expert-token-usage", `+≥${compactNumber(accounting.observed_unsettled_tokens)} observed tokens · unsettled calls; included separately from reported usage and reserves`));
      }
      work.append(element("p", "expert-token-usage", `🔢 ≥${compactNumber(accounting.reported_tokens)} tok · reserve ${compactNumber(accounting.reserved_tokens)} · unknown closed ${accounting.unknown_closed_attempts ?? "?"} · unavailable token records ${accounting.unavailable_token_attempts ?? "?"}`));
      const recovery = object(summary.context_recovery);
      if (recovery.context_failure_attempts > 0) {
        const retries = recovery.recovery_attempts || 0;
        const detail = element("p", "expert-token-usage", `Context recovery: ${retries} attempts · reported ≥${compactNumber(recovery.recovery_reported_tokens)} tokens · usage unknown ${recovery.recovery_unknown_token_attempts ?? "?"}`);
        detail.title = "Subsequent attempts after the first context failure, including successful recovery. Initial failures are separate in command accounting. Unknown usage is not zero; observed usage is not measured savings.";
        work.append(detail);
      }
      work.append(element("p", "panel-copy", `${displayValue(object(summary.experiments).total)} experiments · ${displayValue(object(summary.literature).retrieved)} papers · ${displayValue(object(summary.skills).count)} skills · ${displayValue(object(summary.evolutions).changed)} effective evolutions`));
      card.append(identity, work);
      container.append(card);
    });
    for (const [attemptId, entry] of state.workingTiles) {
      if (seenTiles.has(attemptId) || entry.leaving) continue;
      const tiles = tileLists.get(entry.role);
      if (previousTiles.has(attemptId) && tiles) {
        entry.leaving = true;
        entry.tile.classList.add("leaving");
        tiles.append(entry.tile);
        window.setTimeout(() => { entry.tile.remove(); state.workingTiles.delete(attemptId); }, 300);
      } else {
        state.workingTiles.delete(attemptId);
      }
    }
  }

  function selectByComponent(componentId) {
    state.selectedKey = `component:${componentId}`;
    setDrawerMode("evidence");
    renderGraph();
    renderDrawer();
    openDrawer();
  }

  function renderComponents(snapshot) {
    byId("components-panel").hidden = sharedGoal(snapshot);
    if (sharedGoal(snapshot)) return;
    const components = list(snapshot.components).filter((component) => componentMatches(component));
    const container = byId("component-list");
    container.replaceChildren();
    setText("component-count", components.length);
    if (!components.length) {
      container.append(element("p", "empty-copy", "No component projection."));
      return;
    }
    components.forEach((component) => {
      const card = element(
        "button",
        `entity-card expert-accent ${expertClass(snapshot, component.owner_role)}`,
      );
      card.type = "button";
      card.setAttribute("aria-label", `Inspect component ${component.component_id}`);
      const stateLine = element("p", "state-line");
      stateLine.append(
        element("span", "", compact(component.tier, 24)),
        element("span", component.blocked ? "blocked" : "", compact(component.state, 24)),
      );
      card.append(stateLine);
      card.append(element("h3", "", component.component_id));
      card.append(element("p", "", `owner ${component.owner_role || "—"} · phase ${component.phase || "—"}`));
      if (component.blocked) {
        card.append(element("p", "blocked", `blocked: ${list(component.blocked_reasons).join("; ") || "unspecified"}`));
      } else {
        card.append(element("p", "", `${list(component.open_attempt_ids).length} durably open attempts`));
      }
      if (component.revision_warning) {
        card.append(element("p", "blocked", `repeated revisions without convergence; cause unverified · circuit breaker ${component.revision_warning}: ${component.cycle_revisions ?? 0} revisions in cycle ${component.current_cycle || "—"}`));
      }
      card.addEventListener("click", () => selectByComponent(component.component_id));
      container.append(card);
    });
  }

  function renderActivity(snapshot) {
    const openAttempts = list(snapshot.attempts).filter(
      (attempt) => attempt.state === "OPEN" && attemptMatches(attempt),
    );
    const container = byId("activity-list");
    container.replaceChildren();
    setText("activity-count", openAttempts.length);
    if (!openAttempts.length) {
      container.append(element("p", "empty-copy", "No matching durable OPEN activity."));
      return;
    }
    const groups = new Map();
    openAttempts.forEach((attempt) => {
      const role = taskScopeLabel(attempt);
      if (!groups.has(role)) groups.set(role, []);
      groups.get(role).push(attempt);
    });
    Array.from(groups.entries()).sort(([left], [right]) => left.localeCompare(right)).forEach(([label, attempts]) => {
      const role = attempts[0].task_scope === "expert" ? attempts[0].owner_role : null;
      const card = element("article", `activity-card${role ? ` expert-accent ${expertClass(snapshot, role)}` : ""}`);
      const heading = element("div", "activity-heading-row");
      heading.append(element("h3", "", label), element("span", "counter", attempts.length));
      card.append(heading);
      attempts.forEach((attempt) => {
        const record = element("div", "activity-record");
        record.append(element("strong", "", `${attempt.kind || "—"} / ${attempt.phase || "—"}`));
        record.append(element("span", "", attempt.component_id || attempt.workflow_id || "—"));
        record.append(element("span", "", `${attempt.attempt_id || "—"} · ${formatDuration(attempt.started_at, attempt.ended_at)}`));
        card.append(record);
      });
      container.append(card);
    });
  }

  function renderRendezvous(snapshot) {
    const records = list(snapshot.rendezvous).filter((record) => rendezvousMatches(record));
    const container = byId("rendezvous-list");
    container.replaceChildren();
    setText("rendezvous-count", records.length);
    if (!records.length) {
      container.append(element("p", "empty-copy", "No matching durable rendezvous records."));
      return;
    }
    records.forEach((record) => {
      const card = element("article", "activity-card rendezvous-card");
      const heading = element("div", "activity-heading-row");
      heading.append(
        element("h3", "", record.rendezvous_id || "unnamed rendezvous"),
        element("span", `status-label ${statusClass(record.status)}`, record.status || "—"),
      );
      const target = record.responder_role || record.target_role || "unassigned target";
      card.append(heading);
      card.append(element("p", "relation-line", `${record.requester_role || "—"} → ${target}`));
      card.append(element("p", "", `${record.kind || "—"} · ${record.component_id || "—"} · ${record.phase || "—"}`));
      card.append(element("p", "", record.question || "No question projected."));
      if (record.disposition || record.summary) {
        card.append(element("p", "resolution-line", `${record.disposition || "resolved"} · ${record.summary || "no summary"}`));
      }
      container.append(card);
    });
  }

  function researchGraph(snapshot) {
    if (state.product && state.product.run_id === snapshot.run_id && state.product.graph) return state.product.graph;
    return {nodes: list(snapshot.hypotheses), edges: list(snapshot.hypotheses)
      .filter((item) => item.parent_hypothesis_id)
      .map((item) => ({source: item.parent_hypothesis_id, target: item.hypothesis_id, relation: item.relation}))};
  }

  function appendHypothesisComparison(grid, hypothesis, parent) {
    appendDefinition(grid, "lineage", hypothesis.parent_hypothesis_id ? "Derived hypothesis" : "Root hypothesis");
    if (hypothesis.parent_hypothesis_id) {
      appendDefinition(grid, "parent claim", parent?.claim || "Parent record unavailable");
      appendDefinition(grid, "parent method family", parent?.method_family_id || "Unavailable");
      appendDefinition(grid, "parent scientific assessment", parent ? parent.assessment || "Not assessed" : "Unavailable");
    }
  }

  function graphStatus(item) {
    if (item.kind !== "hypothesis") return {label: item.state, className: statusClass(item.state)};
    const failure = hypothesisFailure(item);
    if (failure) return {label: failure === "Refuted" ? "Refuted" : "Execution failed", className: "state-blocked", failure};
    const assessment = item.raw.assessment || item.raw.state;
    if (assessment === "SUPPORTED") return {label: "Supported", className: "state-accepted"};
    if (assessment === "INCONCLUSIVE") return {label: "Inconclusive", className: "state-other"};
    return {label: "Not assessed", className: "state-pending"};
  }

  function isPruned(item) {
    return item.kind === "hypothesis" && (item.raw.state === "PRUNED" || list(item.raw.pruned_by).length > 0);
  }

  function topologyEdges(includePruning = state.showPruning) {
    const visible = new Set(state.topology.map((item) => item.id));
    return state.topology.flatMap((item) => [
      ...item.dependencies.map((source) => ({source, target: item.id,
        kind: item.kind === "hypothesis" ? "lineage" : "dependency",
        relation: item.kind === "hypothesis" ? item.raw.relation || "parent to child" : "dependency"})),
      ...(includePruning ? list(item.pruningRelations).map((edge) => ({...edge, kind: "pruning"})) : []),
    ]).filter((edge) => visible.has(edge.source) && visible.has(edge.target));
  }

  function relationLabel(edge) {
    return edge.kind === "pruning" ? "Winner → pruned hypothesis"
      : edge.kind === "lineage" ? "Parent → derived hypothesis" : "Dependency → dependent";
  }

  function inspectRelation(edge, source, target) {
    openLocalDetail("relationship", {from: source.id, to: target.id, kind: edge.kind,
      relation: edge.relation, recorded: edge, source: source.raw, target: target.raw});
  }

  function buildTopology(snapshot, mode) {
    const componentViews = new Map(
      list(snapshot.components).map((component) => [String(component.component_id), component]),
    );
    const research = researchGraph(snapshot);
    const hypotheses = list(research.nodes).filter(
      (hypothesis) => hypothesis && typeof hypothesis.hypothesis_id === "string" && hypothesis.hypothesis_id,
    );
    if (mode === "hypothesis") {
      const byHypothesis = new Map(hypotheses.map((hypothesis) => [hypothesis.hypothesis_id, hypothesis]));
      return hypotheses.map((hypothesis) => {
        const componentId = String(hypothesis.component_id || "");
        const component = componentViews.get(componentId) || {};
        const incoming = list(research.edges).filter((edge) => edge.target === hypothesis.hypothesis_id && byHypothesis.has(edge.source));
        const parent = byHypothesis.get(hypothesis.parent_hypothesis_id);
        return {
          key: `hypothesis:${hypothesis.hypothesis_id}`,
          kind: "hypothesis",
          id: hypothesis.hypothesis_id,
          componentId,
          dependencies: parent ? [hypothesis.parent_hypothesis_id] : [],
          pruningRelations: incoming.filter((edge) => edge.relation === "pruned_by"),
          title: hypothesis.claim || hypothesis.hypothesis_id,
          subtitle: `${hypothesis.proposer_role || "unknown proposer"} · ${hypothesis.chain || hypothesis.method_family_id || "method ?"}`,
          detail: [hypothesis.cycle ? `cycle ${hypothesis.cycle}` : "", hypothesis.selected_tip ? "selected" : "", componentId || "component ?"].filter(Boolean).join(" · "),
          state: hypothesis.state || hypothesis.assessment || component.state || "proposed",
          accentClass: expertClass(snapshot, hypothesis.proposer_role),
          role: hypothesis.proposer_role,
          parent,
          raw: hypothesis,
        };
      });
    }

    const goal = object(snapshot.goal);
    const contract = object(goal.research_contract);
    const components = list(contract.components).filter(
      (component) => component && typeof component.id === "string" && component.id,
    );
    return components.map((component) => {
      const view = componentViews.get(component.id) || {};
      return {
        key: `component:${component.id}`,
        kind: "component",
        id: component.id,
        componentId: component.id,
        dependencies: list(component.dependencies).map(String),
        title: component.id,
          subtitle: component.name || `${component.tier || "component"} contract`,
        detail: `${view.phase || "not started"} · owner ${view.owner_role || component.owner || "—"}`,
        state: view.state || "pending",
        accentClass: expertClass(snapshot, view.owner_role || component.owner),
        role: view.owner_role || component.owner,
        raw: { contract: component, view },
      };
    });
  }

  function layoutTopology(items) {
    const byNodeId = new Map(items.map((item) => [item.id, item]));
    const levels = new Map();
    const visiting = new Set();
    function levelFor(item) {
      if (levels.has(item.id)) return levels.get(item.id);
      if (visiting.has(item.id)) return 0;
      visiting.add(item.id);
      const dependencyLevels = item.dependencies
        .map((id) => byNodeId.get(id))
        .filter(Boolean)
        .map((dependency) => levelFor(dependency));
      visiting.delete(item.id);
      const level = dependencyLevels.length ? Math.max(...dependencyLevels) + 1 : 0;
      levels.set(item.id, level);
      return level;
    }
    items.forEach(levelFor);

    const groups = new Map();
    items.forEach((item) => {
      const level = levels.get(item.id) || 0;
      if (!groups.has(level)) groups.set(level, []);
      groups.get(level).push(item);
    });
    groups.forEach((group) => group.sort((left, right) => left.id.localeCompare(right.id)));
    const maxRows = Math.max(1, ...Array.from(groups.values(), (group) => group.length));
    const width = 230;
    const height = items.some((item) => item.kind === "hypothesis") ? 128 : 102;
    const columnGap = 96;
    const rowGap = 34;
    const positioned = [];
    Array.from(groups.keys()).sort((left, right) => left - right).forEach((level) => {
      const group = groups.get(level);
      const offset = ((maxRows - group.length) * (height + rowGap)) / 2;
      group.forEach((item, index) => {
        positioned.push({
          ...item,
          x: level * (width + columnGap),
          y: offset + index * (height + rowGap),
          width,
          height,
        });
      });
    });
    return positioned;
  }

  function applyView() {
    graphViewport.setAttribute(
      "transform",
      `translate(${state.view.x} ${state.view.y}) scale(${state.view.scale})`,
    );
  }

  function fitGraph() {
    if (!state.graphBounds || !graph.clientWidth || !graph.clientHeight) return;
    const padding = 48;
    const width = Math.max(1, state.graphBounds.width);
    const height = Math.max(1, state.graphBounds.height);
    const scale = Math.min(
      1.35,
      Math.max(0.25, Math.min((graph.clientWidth - padding * 2) / width, (graph.clientHeight - padding * 2) / height)),
    );
    state.view.scale = scale;
    state.view.x = (graph.clientWidth - width * scale) / 2 - state.graphBounds.x * scale;
    state.view.y = (graph.clientHeight - height * scale) / 2 - state.graphBounds.y * scale;
    applyView();
  }

  function selectTopology(item) {
    state.selectedKey = item.key;
    setDrawerMode("evidence");
    renderGraph();
    renderDrawer();
    openDrawer();
    announce(`Selected ${item.kind} ${item.id}`);
  }

  function renderFallback(positioned, edges) {
    topologyList.replaceChildren();
    positioned.forEach((item) => {
      const parent = item.kind === "hypothesis"
        ? `parent: ${item.raw.parent_hypothesis_id || "none (root hypothesis)"}`
        : `depends on ${item.dependencies.join(", ") || "nothing"}`;
      const listItem = element("li");
      const button = element("button", "", `${item.id} — ${item.title}; ${parent}; ${graphStatus(item).label}${isPruned(item) ? "; pruned (control decision)" : ""}`);
      button.type = "button";
      button.addEventListener("click", () => selectTopology(item));
      listItem.append(button);
      topologyList.append(listItem);
    });
    const byNodeId = new Map(positioned.map((item) => [item.id, item]));
    edges.forEach((edge) => {
      const listItem = element("li");
      const button = element("button", "", `${relationLabel(edge)}: ${edge.source} → ${edge.target}`);
      button.type = "button";
      button.addEventListener("click", () => inspectRelation(edge, byNodeId.get(edge.source), byNodeId.get(edge.target)));
      listItem.append(button);
      topologyList.append(listItem);
    });
  }

  function highlightConnected(keys) {
    graphNodes.querySelectorAll(".graph-node").forEach((node) => {
      const active = keys.has(node.dataset.key);
      node.classList.toggle("connected", active);
      node.classList.toggle("dimmed", !active);
    });
    graphEdges.querySelectorAll(".graph-edge, .graph-edge-arrow").forEach((edge) => {
      const active = keys.has(edge.dataset.source) && keys.has(edge.dataset.target);
      edge.classList.toggle("connected", active);
      edge.classList.toggle("dimmed", !active);
    });
  }

  function clearConnected() {
    graphNodes.querySelectorAll(".graph-node").forEach((node) => node.classList.remove("connected", "dimmed"));
    graphEdges.querySelectorAll(".graph-edge, .graph-edge-arrow").forEach((edge) => edge.classList.remove("connected", "dimmed"));
    relationTooltip.hidden = true;
  }

  function showRelation(edge, source, target) {
    highlightConnected(new Set([source.key, target.key]));
    relationTooltip.textContent = `${source.id} → ${target.id} · ${relationLabel(edge)}`;
    relationTooltip.hidden = false;
  }

  function showNodeConnections(item) {
    const connected = new Set([item.key]);
    const byNodeId = new Map(state.topology.map((node) => [node.id, node]));
    topologyEdges().forEach((edge) => {
      if (edge.source === item.id || edge.target === item.id) {
        connected.add(byNodeId.get(edge.source).key);
        connected.add(byNodeId.get(edge.target).key);
      }
    });
    highlightConnected(connected);
    const count = connected.size - 1;
    relationTooltip.textContent = `${item.id} · ${count} directly connected node${count === 1 ? "" : "s"}`;
    relationTooltip.hidden = false;
  }

  function renderLegend(snapshot, edges) {
    const legend = byId("graph-legend");
    legend.replaceChildren();
    function group(label, items) {
      if (!items.length) return;
      const section = element("div", "legend-group");
      section.setAttribute("role", "group");
      section.setAttribute("aria-label", label);
      section.append(element("span", "legend-heading", label), ...items);
      legend.append(section);
    }
    group("Relations", ["lineage", "dependency", "pruning"].filter((kind) => edges.some((edge) => edge.kind === kind)).map((kind) => {
      const item = element("span", "legend-item");
      const sample = svgElement("svg", {class: "edge-key", viewBox: "0 0 36 12", "aria-hidden": "true"});
      sample.append(svgElement("path", {class: `graph-edge ${kind === "pruning" ? "pruning-edge" : ""}`, d: "M 1 6 H 28"}),
        svgElement("polygon", {class: "graph-edge-arrow", points: "28,2 35,6 28,10"}));
      item.append(sample, document.createTextNode(relationLabel({kind})));
      return item;
    }));
    const statuses = new Map(state.topology.map((item) => { const status = graphStatus(item); return [status.label, status]; }));
    group("Status", Array.from(statuses.values()).map((status) => {
      const item = element("span", "legend-item");
      const sample = svgElement("svg", {class: `status-key ${status.className}`, viewBox: "12 12 14 14", "aria-hidden": "true"});
      sample.append(graphStatusGlyph(status.failure));
      item.append(sample, document.createTextNode(status.label));
      return item;
    }));
    const controls = [];
    if (state.topology.some(isPruned)) {
      const item = element("span", "legend-item");
      const sample = svgElement("svg", {class: "pruned-key", viewBox: "0 0 32 18", "aria-hidden": "true"});
      sample.append(svgElement("rect", {x: 1, y: 1, width: 30, height: 16, rx: 3}));
      item.append(sample, document.createTextNode("Pruned (control, not refutation)"));
      controls.push(item);
    }
    group("Control", controls);
    group("Experts", Array.from(new Set(state.topology.map((item) => item.role))).map((role) =>
      element("span", `legend-item expert-key ${expertClass(snapshot, role)}`, role || "Unknown expert")));
  }

  function edgeGeometry(source, target, pruning, index) {
    // Keep pruning's lower exit and upper entry separate from the midpoint
    // ports used by lineage arrows, including links within the same column.
    if (pruning && source.x === target.x) {
      const x = source.x + source.width, y1 = source.y + source.height * 0.75, y2 = target.y + target.height * 0.25;
      const bend = x + 56 + (index % 3) * 12;
      return {path: `M ${x} ${y1} C ${bend} ${y1}, ${bend} ${y2}, ${x + 8} ${y2}`,
        arrow: `${x + 8},${y2 - 5} ${x},${y2} ${x + 8},${y2 + 5}`, top: 0, left: 0, right: bend};
    }
    if (pruning) {
      // Use the column gaps and the space above the graph so a later winner
      // can point back to a root without crossing intervening node boxes.
      const y1 = source.y + source.height * 0.75, y2 = target.y + target.height * 0.25;
      const offset = 24 + (index % 3) * 12;
      const x1 = source.x - offset, x2 = target.x - offset, top = -36 - index * 16;
      return {path: `M ${source.x} ${y1} H ${x1} V ${top} H ${x2} V ${y2} H ${target.x - 8}`,
        arrow: `${target.x - 8},${y2 - 5} ${target.x},${y2} ${target.x - 8},${y2 + 5}`,
        top, left: Math.min(0, x1, x2), right: 0};
    }
    const x1 = source.x + source.width, y1 = source.y + source.height / 2;
    const x2 = target.x, y2 = target.y + target.height / 2;
    const bend = Math.max(36, (x2 - x1) / 2);
    return {path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2 - 8} ${y2}`,
      arrow: `${x2 - 8},${y2 - 5} ${x2},${y2} ${x2 - 8},${y2 + 5}`, top: 0, left: 0, right: 0};
  }

  function renderGraph() {
    // Hidden SVG has no usable text metrics. The existing render cache will
    // rebuild it on entry to the map, including selection from Operations.
    if (state.activeView !== "map") { state.renderInputs.delete("map"); return; }
    graphEdges.replaceChildren();
    graphNodes.replaceChildren();
    const positioned = layoutTopology(state.topology);
    const edges = topologyEdges();
    renderFallback(positioned, edges);
    renderLegend(state.snapshot, edges);
    clearConnected();
    graphEmpty.hidden = positioned.length > 0;
    if (!positioned.length) {
      state.graphBounds = null;
      return;
    }
    const byNodeId = new Map(positioned.map((item) => [item.id, item]));
    let extraTop = 0, extraLeft = 0, extraRight = 0, pruningIndex = 0;
    edges.forEach((record) => {
      const source = byNodeId.get(record.source), target = byNodeId.get(record.target);
      const pruning = record.kind === "pruning";
      const geometry = edgeGeometry(source, target, pruning, pruning ? pruningIndex++ : 0);
      extraTop = Math.min(extraTop, geometry.top);
      extraLeft = Math.min(extraLeft, geometry.left);
      extraRight = Math.max(extraRight, geometry.right);
      const attributes = {"data-relation-kind": record.kind, "data-source": source.key, "data-target": target.key};
      const edge = svgElement("path", {
        class: `graph-edge ${pruning ? "pruning-edge" : ""}`, d: geometry.path,
        tabindex: "0", role: "img", ...attributes,
        "aria-label": `${source.id} to ${target.id}, ${relationLabel(record)}`,
      });
      const edgeTitle = svgElement("title");
      edgeTitle.textContent = `${source.id} → ${target.id} · ${relationLabel(record)}`;
      edge.append(edgeTitle);
      edge.addEventListener("mouseenter", () => showRelation(record, source, target));
      edge.addEventListener("mouseleave", clearConnected);
      edge.addEventListener("focus", () => showRelation(record, source, target));
      edge.addEventListener("blur", clearConnected);
      edge.addEventListener("click", () => inspectRelation(record, source, target));
      edge.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); inspectRelation(record, source, target); }
      });
      graphEdges.append(edge, svgElement("polygon", {class: "graph-edge-arrow", points: geometry.arrow, ...attributes}));
    });

    positioned.forEach((item) => {
      const status = graphStatus(item);
      const failure = status.failure;
      const lineage = item.kind === "hypothesis" ? (item.raw.parent_hypothesis_id ? "Derived hypothesis" : "Root hypothesis") : "";
      const control = isPruned(item) ? " · pruned" : "";
      const group = svgElement("g", {
        class: `graph-node ${item.accentClass} ${status.className}${isPruned(item) ? " is-pruned" : ""}${state.selectedKey === item.key ? " selected" : ""}`,
        transform: `translate(${item.x} ${item.y})`,
        tabindex: "0",
        role: "button",
        "aria-label": `${failure ? `× ${failure}, ` : ""}${item.kind} ${item.id}, ${lineage}, ${item.title}, ${status.label}${control}`,
        "data-key": item.key,
      });
      group.append(svgElement("rect", { class: "node-body", width: item.width, height: item.height, rx: 4 }));
      group.append(graphStatusGlyph(failure));
      const identifier = svgElement("text", { class: "node-id", x: 32, y: 23 });
      const title = svgElement("text", { class: "node-detail", x: 14, y: 44 });
      const subtitle = svgElement("text", { class: "node-subtitle", x: 14, y: 77 });
      const detail = svgElement("text", { class: "node-subtitle", x: 14, y: 92 });
      const labels = [[identifier, item.id, 1], [title, item.title, 2],
        [subtitle, item.subtitle, 1], [detail, `${status.label} · ${item.detail}`, 1]];
      if (lineage) {
        const label = svgElement("text", {class: "node-lineage", x: 14, y: 117});
        labels.push([label, `${lineage}${control}`, 1]);
        group.append(label);
      }
      const tooltip = svgElement("title");
      tooltip.textContent = `${item.id}: ${item.title} · ${item.subtitle} · ${status.label} · ${item.detail} · ${lineage}${control}`;
      group.append(identifier, title, subtitle, detail, tooltip);
      group.addEventListener("click", (event) => {
        event.stopPropagation();
        selectTopology(item);
      });
      group.addEventListener("mouseenter", () => showNodeConnections(item));
      group.addEventListener("mouseleave", clearConnected);
      group.addEventListener("focus", () => showNodeConnections(item));
      group.addEventListener("blur", clearConnected);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectTopology(item);
        }
      });
      graphNodes.append(group);
      labels.forEach(([text, value, lines]) => fitNodeText(text, value, item.width - Number(text.getAttribute("x")) - 14, lines));
    });

    const right = Math.max(...positioned.map((item) => item.x + item.width));
    const bottom = Math.max(...positioned.map((item) => item.y + item.height));
    state.graphBounds = { x: extraLeft, y: extraTop, width: Math.max(right, extraRight) - extraLeft, height: bottom - extraTop };
    applyView();
    if (state.fittedMode !== state.topologyMode) {
      state.fittedMode = state.topologyMode;
      window.requestAnimationFrame(fitGraph);
    }
  }

  function renderTopology(snapshot) {
    const hypotheses = list(researchGraph(snapshot).nodes);
    setText("hypothesis-count", hypotheses.length);
    byId("show-hypotheses").disabled = hypotheses.length === 0;
    byId("show-components").hidden = sharedGoal(snapshot);
    if (sharedGoal(snapshot)) state.topologyMode = "hypothesis";
    else if (!hypotheses.length && state.topologyMode === "hypothesis") state.topologyMode = "component";
    const allItems = buildTopology(snapshot, state.topologyMode);
    state.topology = allItems.filter((item) => topologyMatches(item));
    const hypothesisMode = state.topologyMode === "hypothesis";
    const pruningCount = topologyEdges(true).filter((edge) => edge.kind === "pruning").length;
    const pruningControl = byId("toggle-pruning");
    pruningControl.hidden = !hypothesisMode;
    pruningControl.disabled = !pruningCount;
    pruningControl.textContent = `${state.showPruning ? "Hide" : "Show"} pruning links (${pruningCount})`;
    byId("show-components").classList.toggle("active", !hypothesisMode);
    byId("show-components").setAttribute("aria-pressed", String(!hypothesisMode));
    byId("show-hypotheses").classList.toggle("active", hypothesisMode);
    byId("show-hypotheses").setAttribute("aria-pressed", String(hypothesisMode));
    byId("graph-heading").textContent = hypothesisMode ? "Hypothesis lineage" : "Component dependencies";
    const filterNote = state.topology.length < allItems.length
      ? " Filtered endpoints and their edges are omitted."
      : "";
    byId("graph-summary").textContent = hypothesisMode
      ? `${state.topology.length} of ${allItems.length} durable hypotheses shown. Solid arrows: parent → derived hypothesis. Dashed arrows: winner → pruned hypothesis (${state.showPruning ? "shown" : "hidden"}). Pruning does not change ancestry or scientific assessment. Select a node or link for recorded revision details.${filterNote}`
      : `${state.topology.length} of ${allItems.length} contract components shown. Edges point from dependency to dependent.${filterNote}`;
    byId("graph-title").textContent = hypothesisMode ? "Hypothesis lineage graph" : "Component dependency graph";
    const selectedExists = state.topology.some((item) => item.key === state.selectedKey);
    if (state.selectedKey && state.selectedKey.startsWith(`${state.topologyMode}:`) && !selectedExists) {
      state.selectedKey = null;
    }
    renderGraph();
  }

  function selectionRecord() {
    const topologyItem = state.topology.find((item) => item.key === state.selectedKey);
    if (topologyItem) return topologyItem;
    if (state.selectedKey && state.selectedKey.startsWith("component:") && state.snapshot) {
      const componentId = state.selectedKey.slice("component:".length);
      const view = list(state.snapshot.components).find((item) => item.component_id === componentId);
      if (view) {
        return {
          key: state.selectedKey,
          kind: "component",
          id: componentId,
          componentId,
          title: componentId,
          state: view.state,
          raw: { view, contract: {} },
        };
      }
    }
    return null;
  }

  function renderSelection(selection) {
    const container = byId("selection-detail");
    const heading = element("h3", "", "Selection");
    heading.id = "selection-heading";
    container.replaceChildren(heading);
    if (!selection) {
      container.append(element("p", "", "Select a graph node or component to narrow the durable record."));
      return;
    }
    const grid = element("dl", "selection-grid");
    appendDefinition(grid, "kind", selection.kind);
    appendDefinition(grid, "id", selection.id);
    appendDefinition(grid, "component", selection.componentId || "—");
    appendDefinition(grid, "state", selection.state || "—");
    if (selection.kind === "hypothesis") {
      const hypothesis = object(selection.raw);
      appendHypothesisComparison(grid, hypothesis, selection.parent);
      appendDefinition(grid, "scientific assessment", hypothesis.assessment || "Not assessed");
      appendDefinition(grid, "claim", hypothesis.claim || "—");
      appendDefinition(grid, "proposer", hypothesis.proposer_role || "—");
      appendDefinition(grid, "method family", hypothesis.method_family_id || "—");
      appendDefinition(grid, "relation", hypothesis.relation || "root");
      appendDefinition(grid, "parent", hypothesis.parent_hypothesis_id || "—");
      appendDefinition(grid, "assessment", hypothesis.assessment_id || "—");
      appendDefinition(grid, "assumptions", list(hypothesis.assumptions).join("; ") || "—");
    } else {
      const raw = object(selection.raw);
      const view = object(raw.view);
      const contract = object(raw.contract);
      appendDefinition(grid, "owner", view.owner_role || contract.owner || "—");
      appendDefinition(grid, "phase", view.phase || "—");
      appendDefinition(grid, "tier", view.tier || contract.tier || "—");
      appendDefinition(grid, "blocked", view.blocked ? list(view.blocked_reasons).join("; ") || "yes" : "no");
    }
    container.append(grid);
    const sessions = element("button", "detail-link", "Browse related sessions");
    sessions.type = "button";
    sessions.addEventListener("click", () => {
      closeDrawer();
      openWorkbench("sessions", {component_id: selection.componentId});
    });
    container.append(sessions);
  }

  function renderAttempts(selection) {
    const allAttempts = list(state.snapshot && state.snapshot.attempts);
    const scopedAttempts = selection && selection.componentId
      ? allAttempts.filter((attempt) => attempt.component_id === selection.componentId)
      : allAttempts;
    const attempts = scopedAttempts.filter((attempt) => attemptMatches(attempt));
    attempts.sort((left, right) => Number(right.started_at || 0) - Number(left.started_at || 0));
    setText("attempt-count", attempts.length);
    const container = byId("attempt-list");
    container.replaceChildren();
    if (!attempts.length) {
      container.append(element("p", "empty-copy", "No durable attempts in this scope."));
      return;
    }
    attempts.forEach((attempt) => {
      const card = element("article", "attempt-card");
      card.append(element("h4", "", attempt.attempt_id || "unnamed attempt"));
      const grid = element("dl");
      appendDefinition(grid, "state", attempt.state === "OPEN" ? "OPEN (durable; liveness unknown)" : attempt.state || "—");
      appendDefinition(grid, "kind / phase", `${attempt.kind || "—"} / ${attempt.phase || "—"}`);
      appendDefinition(grid, "task ownership", taskScopeLabel(attempt));
      appendDefinition(grid, "execution role", attempt.role || "—");
      appendDefinition(grid, "ordinal", attempt.attempt_ordinal ?? "—");
      appendDefinition(grid, "workflow", attempt.workflow_id || "—");
      appendDefinition(grid, "started", formatTimestamp(attempt.started_at));
      appendDefinition(grid, "duration", formatDuration(attempt.started_at, attempt.ended_at));
      appendDefinition(grid, "terminal reason", attempt.terminal_reason || "—");
      appendDefinition(grid, "failure code", attempt.failure_code || "—");
      appendDefinition(grid, "return code", attempt.returncode ?? "—");
      appendDefinition(grid, "diagnostic", attempt.diagnostic_summary || "—");
      appendDefinition(grid, "stdout log", attempt.stdout_path || "—");
      appendDefinition(grid, "stderr log", attempt.stderr_path || "—");
      const devices = list(attempt.gpu_usage).length ? list(attempt.gpu_usage)
        : attempt.gpu_uuid ? [{...attempt, pressure_exceeded: attempt.resource_pressure_exceeded}] : [];
      devices.forEach((device) => {
        appendDefinition(grid, "GPU", device.gpu_uuid);
        appendDefinition(grid, "minimum free VRAM", `${device.minimum_free_memory_mib ?? "?"} MiB`);
        appendDefinition(grid, "latest free VRAM", `${device.latest_free_memory_mib ?? "?"} MiB`);
        appendDefinition(grid, "latest utilization", `${device.latest_utilization_percent ?? "?"}%`);
        if (device.owned_memory) {
          const own = object(device.owned_memory);
          appendDefinition(grid, "own sampled latest / peak", `${own.latest_sample_memory_mib ?? "?"} / ${own.sampled_peak_memory_mib ?? "?"} MiB`);
          appendDefinition(grid, "own sample coverage", `${own.coverage ?? "unknown"}; unknown ${own.unknown_sample_count ?? "?"} / ${own.sample_count ?? "?"} samples`);
        }
        appendDefinition(
          grid,
          "reserve pressure",
          device.pressure_exceeded === true
            ? "EXCEEDED"
            : device.pressure_exceeded === false ? "not exceeded" : "unknown",
        );
        if (device.pressure_exceeded === true) card.classList.add("pressure-exceeded");
      });
      card.append(grid);
      const inspect = element("button", "detail-link", "Session / tools / corrections");
      inspect.type = "button";
      inspect.addEventListener("click", () => openRemoteDetail("attempt", {attempt_id: attempt.attempt_id}));
      card.append(inspect);
      container.append(card);
    });
  }

  function renderFactCounts(container) {
    container.replaceChildren();
    Object.entries(object(state.snapshot && state.snapshot.fact_counts))
      .sort(([left], [right]) => left.localeCompare(right))
      .forEach(([factType, count]) => appendDefinition(container, factType, count));
    if (!container.childElementCount) appendDefinition(container, "durable facts", "none projected");
  }

  function renderOracle(container) {
    const oracle = object(state.snapshot && state.snapshot.oracle);
    const audit = object(oracle.observation);
    container.replaceChildren();
    container.className = `oracle-detail ${oracle.ok === true ? "ok" : "issue"}`;
    if (audit.state === "pending" || audit.state === "error") {
      container.append(element("strong", "", audit.state === "pending" ? "Independent oracle: audit pending" : "Independent oracle: audit unavailable"));
      container.append(element("p", "", "No current audit result is available. This does not establish integrity or scientific success."));
      return;
    }
    container.append(element("p", "panel-copy", `Historical audit: ${formatTimestamp(audit.audited_at)}${audit.refreshing ? " · refresh pending" : ""}. Audits describe their read and do not establish current integrity.`));
    if (oracle.ok === true) {
      container.append(element("strong", "", "Independent oracle: last audit clean"));
      container.append(element("p", "", "No integrity issues were projected for this run. CLEAN is not a scientific-success verdict."));
      return;
    }
    const issues = list(oracle.issues);
    container.append(element("strong", "", `Independent oracle: ${issues.length} issue${issues.length === 1 ? "" : "s"}`));
    const issueList = element("ul", "evidence-list");
    issues.forEach((issue) => {
      const record = object(issue);
      issueList.append(element("li", "", record.message || record.code || compact(issue, 120)));
    });
    if (!issues.length) issueList.append(element("li", "", "Oracle status is unavailable."));
    container.append(issueList);
  }

  function renderEvidenceOverview() {
    renderFactCounts(byId("evidence-page-fact-counts"));
    renderOracle(byId("evidence-page-oracle"));
  }

  function renderEvidence(selection) {
    const hypothesisEvidence = byId("hypothesis-evidence");
    hypothesisEvidence.replaceChildren();
    if (selection && selection.kind === "hypothesis") {
      const hypothesis = object(selection.raw);
      const title = element("p", "", `Hypothesis evidence · assessment ${hypothesis.assessment_id || "not recorded"}`);
      hypothesisEvidence.append(title);
      const evidenceIds = list(hypothesis.evidence_ids);
      const evidenceList = element("ul", "evidence-list");
      if (evidenceIds.length) {
        evidenceIds.forEach((evidenceId) => evidenceList.append(element("li", "", evidenceId)));
      } else {
        evidenceList.append(element("li", "", "No evidence IDs projected."));
      }
      const assessment = hypothesis.assessment;
      if (assessment !== undefined && assessment !== null) {
        let assessmentText;
        if (typeof assessment === "object") {
          try {
            assessmentText = JSON.stringify(assessment);
          } catch (_error) {
            assessmentText = "Assessment object is not displayable.";
          }
        } else {
          assessmentText = String(assessment);
        }
        evidenceList.append(element("li", "", `Assessment: ${assessmentText}`));
      }
      hypothesisEvidence.append(evidenceList);
    }

    renderFactCounts(byId("fact-counts"));
    renderOracle(byId("oracle-detail"));
  }

  function renderDrawer() {
    if (!state.snapshot) return;
    const selection = selectionRecord();
    renderSelection(selection);
    renderAttempts(selection);
    renderEvidence(selection);
  }

  function openDrawer() {
    drawer.hidden = false;
    drawerBackdrop.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => byId("close-drawer").focus());
  }

  function closeDrawer() {
    drawer.hidden = true;
    drawerBackdrop.hidden = true;
    drawer.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }

  function renderFilterSummary(snapshot) {
    const componentCount = list(snapshot.components).filter((component) => componentMatches(component)).length;
    const expertCount = list(snapshot.experts).filter((expert) => expertMatches(expert)).length;
    const attemptCount = list(snapshot.attempts).filter((attempt) => attemptMatches(attempt)).length;
    const rendezvousCount = list(snapshot.rendezvous).filter((record) => rendezvousMatches(record)).length;
    byId("filter-summary").textContent =
      (sharedGoal(snapshot) ? "One shared goal · " : `${componentCount}/${list(snapshot.components).length} components · `) +
      `${expertCount}/${list(snapshot.experts).length} experts · ` +
      `${attemptCount}/${list(snapshot.attempts).length} Attempts · ` +
      `${rendezvousCount}/${list(snapshot.rendezvous).length} rendezvous match. Filters affect cards, activity, map nodes, and drawer Attempts.`;
  }

  function renderFilteredViews() {
    if (!state.snapshot) return;
    state.fittedMode = null;
    renderVisibleViews();
    renderFilterSummary(state.snapshot);
    if (state.activeView === "workbench") renderWorkbench();
  }

  function renderChanged(name, input, draw) {
    // Ephemeral display inputs only; durable facts and their verdicts stay authoritative.
    const serialized = JSON.stringify(input);
    if (state.renderInputs.get(name) === serialized) return false;
    state.renderInputs.set(name, serialized);
    draw();
    return true;
  }

  function renderVisibleViews() {
    const snapshot = state.snapshot;
    if (!snapshot || document.hidden) return;
    const filters = selectedFilters();
    const product = object(state.product);
    if (!drawer.hidden && state.detail?.kind === "expert") {
      const role = state.detail.data.role;
      const summary = list(object(snapshot.overview || product.summary).experts).find((item) => item.role === role);
      renderChanged("expert-detail", [role, summary, snapshot.overview?.host_observation?.availability,
        snapshot.status_source, snapshot.status?.dispatch_error], () => renderResearchDetail());
    }
    if (!drawer.hidden && state.detail?.kind === "stage_accounting") {
      renderChanged("stage-tokens", snapshot.overview?.stage_accounting, renderResearchDetail);
    }
    if (!drawer.hidden && state.detail?.kind === "global_tasks") {
      renderChanged("global-detail", [state.detail.data.task_scope, snapshot.overview?.global_tasks,
        snapshot.overview?.unknown_tasks, snapshot.overview?.grounding_sharing], renderResearchDetail);
    }
    if (state.activeView === "operations") {
      renderChanged("global-tasks", [snapshot.overview?.global_tasks, snapshot.overview?.unknown_tasks, snapshot.overview?.grounding_sharing], () => renderGlobalTasks(snapshot));
      renderChanged("mission", [snapshot.goal, snapshot.status_source, snapshot.status?.grounding_dialogue, snapshot.status?.shared_acceptance, snapshot.status?.goal_verdict, snapshot.hypotheses], () => renderMission(snapshot));
      renderChanged("runtime", [snapshot.status_source, snapshot.status], () => renderRuntime(snapshot));
      renderChanged("experts", [snapshot.experts, snapshot.components, product.experts, snapshot.overview?.experts, snapshot.overview?.host_observation?.availability, snapshot.status_source, snapshot.status?.dispatch_error, product.summary?.experts, snapshot.attempts, filters, sharedGoal(snapshot)], () => {
        const byRole = new Map(list(product.experts).map((item) => [item.role, item]));
        renderExperts({...snapshot, experts: list(snapshot.experts).map((item) => ({...item, ...object(byRole.get(item.role))}))});
      });
      renderChanged("components", [snapshot.components, snapshot.attempts, snapshot.rendezvous, filters, sharedGoal(snapshot)], () => renderComponents(snapshot));
      renderChanged("activity", [snapshot.attempts, snapshot.experts, filters], () => renderActivity(snapshot));
      renderChanged("rendezvous", [snapshot.rendezvous, filters], () => renderRendezvous(snapshot));
    } else if (state.activeView === "map") {
      if (sharedGoal(snapshot)) state.topologyMode = "hypothesis";
      renderChanged("map", [snapshot.components, snapshot.experts, snapshot.goal, researchGraph(snapshot), filters, state.topologyMode, state.showPruning, state.selectedKey], () => renderTopology(snapshot));
    } else if (state.activeView === "evidence") {
      renderChanged("evidence", [snapshot.fact_counts, snapshot.oracle], renderEvidenceOverview);
    } else if (state.activeView === "activity" && state.product) {
      setText("product-status", `Observed ${product.run_id} · ${formatTimestamp(product.observed_at)}`);
      renderChanged("product", [product.experiments, product.events, product.final_report, product.experiments_page, product.events_page, state.history], () => renderProduct(product));
    } else if (state.activeView === "workbench" && state.workbenchKind === "reliability") {
      renderChanged("reliability", snapshot.overview?.reliability, renderWorkbench);
    } else if (state.activeView === "workbench") {
      renderWorkbench();
    }
    if (!drawer.hidden && state.drawerMode === "evidence") {
      renderChanged("drawer", [state.selectedKey, snapshot.attempts, snapshot.fact_counts, snapshot.oracle, filters], () => {
        const top = drawer.scrollTop;
        renderDrawer();
        drawer.scrollTop = top;
      });
    }
  }

  function setActiveView(name, focusTab = false) {
    state.activeView = name;
    document.querySelectorAll("[data-view-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.viewPanel !== name;
    });
    document.querySelectorAll("[role='tab'][data-view]").forEach((tab) => {
      const active = tab.dataset.view === name;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      if (active && focusTab) tab.focus();
    });
    renderVisibleViews();
    if (name === "map") window.requestAnimationFrame(fitGraph);
    if (name === "workbench") loadWorkbench();
    if (name !== "activity" && state.logSocket) {
      state.logSocket.close(); state.logSocket = null;
    } else if (name === "activity" && state.selectedExperiment) openExperimentLog(state.selectedExperiment);
    announce(`Showing ${name === "map" ? "Research Map" : name} view`);
  }

  function setTopologyMode(mode) {
    if (!state.snapshot || mode === state.topologyMode) return;
    if (mode === "component" && sharedGoal()) return;
    if (mode === "hypothesis" && !list(researchGraph(state.snapshot).nodes).length) return;
    state.topologyMode = mode;
    state.selectedKey = null;
    state.fittedMode = null;
    renderVisibleViews();
    renderDrawer();
    window.requestAnimationFrame(fitGraph);
    announce(`Showing ${mode} topology`);
  }

  function render(snapshot, product) {
    state.snapshot = snapshot;
    if (product) state.product = {...product, summary: snapshot.overview || product.summary};
    populateFilters(snapshot);
    renderHud(snapshot);
    renderVisibleViews();
    renderFilterSummary(snapshot);
    renderResearchTotals(object(snapshot.overview || state.product?.summary));
  }

  function openExperimentLog(identity) {
    if (state.logSocket) state.logSocket.close();
    state.selectedExperiment = identity;
    const stream = byId("log-stream").value;
    const url = new URL(`/api/experiments/${encodeURIComponent(identity)}/logs/ws?stream=${stream}`, window.location.href);
    url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url, ["hypoloom.logs", `bearer.${accessToken}`]);
    state.logSocket = socket;
    setText("log-connection", `Connecting to ${identity} / ${stream}…`);
    socket.onmessage = (event) => {
      if (state.logSocket !== socket) return;
      try {
        const log = JSON.parse(event.data);
        const panel = byId("live-log-text");
        const follow = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 32;
        panel.textContent = log.available ? log.text || "(empty log)" : "Log is not available yet; waiting for the Process Attempt.";
        if (follow) panel.scrollTop = panel.scrollHeight;
        setText("log-connection", `${identity} · ${stream} · ${log.size || 0} bytes${log.truncated ? " · showing bounded tail" : ""}`);
      } catch (_error) { setText("log-connection", "Invalid log message; reconnect to retry."); }
    };
    socket.onclose = () => { if (state.logSocket === socket) setText("log-connection", "Log connection closed. Select the experiment to reconnect."); };
    socket.onerror = () => { if (state.logSocket === socket) setText("log-connection", "Log subscription unavailable; check authentication and experiment state."); };
  }

  const WORKBENCH_TITLES = {
    skills: "Skills & evolution", literature: "Literature", sessions: "Sessions & tools",
    interactions: "Leader & Debate", resources: "Experiments & resources",
    reliability: "Execution reliability",
  };
  const WORKBENCH_KEYS = {skills: "skills", literature: "papers", sessions: "sessions", interactions: "interactions", resources: "experiments"};
  const DETAIL_PAGE_SIZE = 50;

  function displayValue(value) {
    if (value === undefined || value === null) return "unavailable";
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  }

  function renderResearchTotals(summary) {
    renderChanged("reliability-preview", summary.reliability?.metrics, () => {
      const preview = byId("run-reliability-preview");
      preview.replaceChildren();
      compactReliability(preview, object(summary.reliability).metrics, {expanded: true});
    });
    const totals = object(summary.totals);
    const cards = [
      ["experiments", "Experiments", object(totals.experiments).total, "resources", "Distinct experiment IDs"],
      ["hypotheses", "Hypotheses", totals.hypotheses, "map", "Accepted hypothesis identities"],
      ["literature", "Literature", object(totals.literature).retrieved, "literature", `${displayValue(object(totals.literature).pdf_available)} PDFs with extracted evidence`],
      ["skills", "Skills", object(totals.skills).count, "skills", `${object(totals.skills).coverage || "unavailable"} accepted/bound catalog`],
      ["evolutions", "Effective evolution", object(totals.evolutions).changed, "skills", `${displayValue(object(totals.evolutions).completed)} completed / ${displayValue(object(totals.evolutions).failed)} failed; changed commits only`],
      ["leader", "Leader", object(totals.leader).started, "interactions", `${displayValue(object(totals.leader).active)} active logical occurrences`],
      ["debate", "Debate", object(totals.debate).started, "interactions", `${displayValue(object(totals.debate).active)} active logical occurrences`],
    ];
    const container = byId("research-totals");
    // Update persistent controls in place so polling never steals keyboard focus.
    cards.forEach(([key, title, value, destination, note]) => {
      let button = byId(`total-${key}`);
      if (!button) {
        button = element("button", "summary-tile");
        button.id = `total-${key}`;
        button.type = "button";
        button.append(element("span", "eyebrow", title), element("strong"), element("span", "panel-copy"));
        button.addEventListener("click", () => {
          if (destination === "map") { setActiveView("map"); setTopologyMode("hypothesis"); }
          else openWorkbench(destination);
        });
        container.append(button);
      }
      button.children[1].textContent = value === undefined || value === null ? "?" : String(value);
      button.children[2].textContent = note;
    });
    const elapsed = summary.elapsed_seconds;
    const wait = summary.waiting_human_seconds;
    const host = object(object(state.snapshot && state.snapshot.overview).host_observation || summary.host_observation);
    const halt = object(host.last_recorded_exception);
    setText("research-summary-source", `All-run totals · elapsed ${elapsed == null ? "unavailable" : formatDuration(0, elapsed)} · human wait ${wait == null ? "unavailable" : formatDuration(0, wait)} · observed ${formatTimestamp(summary.observed_at)}. Host contact: ${host.availability || "unconfirmed"}; OPEN is not liveness. ${halt.exception_type ? `Historical Host exception: ${halt.exception_type} at ${halt.timestamp}; diagnostics/host.jsonl. ` : ""}Filters and event pagination do not change these totals.`);
    let costs = byId("command-retry-costs");
    if (!costs) {
      costs = actionButton("Command retries / token usage", () => {
        const current = object(state.snapshot && state.snapshot.overview);
        const usageFields = (value) => Object.fromEntries(Object.entries(object(value))
          .filter(([key]) => !/cost|pricing|usd/i.test(key)));
        const commands = list(current.stages).map((stage) => ({...stage,
          accounting: usageFields(stage.accounting), context_recovery: usageFields(stage.context_recovery)}));
        openLocalDetail("command_accounting", {notice: "Observed token lower bounds only; reservations and unknown usage are not measured consumption.", commands});
      });
      costs.id = "command-retry-costs";
      container.append(costs);
    }
    if (!byId("stage-token-usage")) {
      const button = actionButton("Tokens by stage", () => {
        const current = object(state.snapshot?.overview || state.product?.summary);
        openLocalDetail("stage_accounting", {stages: current.stage_accounting});
      });
      button.id = "stage-token-usage"; container.append(button);
    }
    if (!byId("execution-reliability")) {
      const button = actionButton("Execution reliability", () => openWorkbench("reliability"));
      button.id = "execution-reliability"; container.append(button);
    }
  }

  const RELIABILITY_METRICS = [
    ["output_first_pass", "First output pass"], ["correction_recovery", "Correction recovery"],
    ["node_retry", "Nodes needing retry"], ["node_delivery", "Final delivery"],
    ["required_actions", "Required actions"],
  ];

  const RELIABILITY_SHORT = ["🎯 FP", "🔧 CR", "🔁 RT", "📦 DL", "🧰 RA"];

  function compactReliability(parent, metrics, {role = "", expanded = false, taskScope = ""} = {}) {
    RELIABILITY_METRICS.forEach(([key, label], index) => {
      const metric = object(object(metrics)[key]);
      const rate = typeof metric.rate === "number" ? `${(metric.rate * 100).toFixed(0)}%` : "—";
      const coverage = [["unknown", "?"], ["excluded", "−"]].filter(([name]) => metric[name] !== 0)
        .map(([name, symbol]) => `${symbol}${metric[name] ?? "?"}`).join(" ");
      const button = actionButton("", () => {
        state.reliabilityMetric = key;
        state.reliabilityFilters = {role: "", owner_role: role, task_scope: taskScope || (role ? "expert" : ""), node: "", model: ""};
        openWorkbench("reliability");
      }, "reliability-mini");
      button.dataset.previewMetric = key;
      button.title = `${label}${key === "node_retry" ? " (lower is better)" : ""}: ${reliabilityMetricText(metric)}. Cumulative within this run; click for denominators and evidence.`;
      button.setAttribute("aria-label", button.title);
      button.append(element("span", "mini-label", RELIABILITY_SHORT[index]));
      if (expanded) button.append(element("span", "mini-name", label));
      button.append(element("strong", "", `${metric.numerator ?? "?"}/${metric.denominator ?? "?"}`),
        element("span", "mini-rate", `${rate}${coverage ? ` · ${coverage}` : ""}`));
      parent.append(button);
    });
  }

  function reliabilityMetricText(metric) {
    const value = object(metric);
    const rate = typeof value.rate === "number" ? `${(value.rate * 100).toFixed(1)}%` : "unavailable";
    return `${displayValue(value.numerator)}/${displayValue(value.denominator)} · ${rate} · unknown ${displayValue(value.unknown)} · excluded ${displayValue(value.excluded)}`;
  }

  function reliabilityMetrics(parent, metrics, scope = null) {
    RELIABILITY_METRICS.forEach(([key, label]) => {
      const button = actionButton(`${label}: ${reliabilityMetricText(object(metrics)[key])}`, () => {
        state.reliabilityMetric = key;
        if (scope) state.reliabilityFilters = {role: scope.role || "", task_scope: scope.task_scope || "", owner_role: scope.owner_role || "", node: scope.node || "", model: scope.model || ""};
        renderReliability();
      }, "reliability-metric detail-link");
      button.dataset.reliabilityMetric = key;
      button.setAttribute("aria-pressed", String(state.reliabilityMetric === key));
      parent.append(button);
    });
  }

  function reliabilityCosts(parent, costs) {
    const value = object(costs);
    parent.append(element("p", "reliability-costs panel-copy", `Retry overhead: ${displayValue(value.retry_attempts)} extra Attempts · reported ≥${displayValue(value.retry_reported_tokens)} tokens · reserve ${displayValue(value.retry_reserved_tokens)} tokens · unavailable token records ${displayValue(value.retry_unavailable_token_attempts)} · unknown closed ${displayValue(value.retry_unknown_closed_attempts)} · elapsed ${value.retry_elapsed_seconds == null ? "unavailable" : formatDuration(0, value.retry_elapsed_seconds)}. Correction-only tokens: unavailable. Unknown does not mean zero; reserves are not measured usage.`));
    parent.append(element("p", "panel-copy", `unknown retry nodes ${displayValue(value.retry_unknown_nodes)} · unavailable reserves ${displayValue(value.retry_unavailable_reservations)} · unknown elapsed ${displayValue(value.retry_elapsed_unknown_attempts)} Attempts.`));
  }

  function renderReliability() {
    const source = object(object(state.snapshot && state.snapshot.overview).reliability);
    const groups = list(source.groups), nodes = list(source.nodes);
    const filters = state.reliabilityFilters;
    const container = byId("workbench-list");
    const renderedKey = JSON.stringify(["reliability", source, filters, state.reliabilityMetric]);
    setText("workbench-scope", "All-run metrics use recorded denominators. Task ownership, execution role, node and model filters narrow evidence below; they do not recalculate rates.");
    setText("workbench-status", Object.keys(source).length ? "Execution reliability, not a model capability score. Read-only recorded evidence." : "Reliability unavailable for this snapshot; missing evidence is unknown.");
    setText("workbench-page", "Recorded node evidence; no event-pagination denominator");
    byId("workbench-previous").disabled = true; byId("workbench-next").disabled = true;
    if (container.dataset.renderedKey === renderedKey) return;
    container.dataset.renderedKey = renderedKey; container.replaceChildren();
    const summary = element("section", "detail-card"); summary.id = "reliability-summary";
    summary.append(element("h3", "", "All-run execution reliability"));
    reliabilityMetrics(summary, source.metrics);
    reliabilityCosts(summary, source.costs);
    const counts = object(source.counts);
    summary.append(element("p", "panel-copy", `Timeouts ${displayValue(counts.timed_out_attempts)} · cancelled ${displayValue(counts.cancelled_attempts)} · tool errors ${displayValue(counts.tool_errors)}/${displayValue(counts.tool_calls)}${counts.trace_unknown_attempts === 0 ? "" : " (observed lower bounds)"} · unknown traces ${displayValue(counts.trace_unknown_attempts)}. Scientific refutation and expected test failures are not model failures.`));
    list(source.notices).forEach((notice) => summary.append(element("p", "panel-copy", displayValue(notice))));
    container.append(summary);
    const controls = element("div", "filter-grid");
    ["task_scope", "owner_role", "role", "node", "model"].forEach((key) => {
      const label = element("label", "", ({role: "Execution role", owner_role: "Owning expert", task_scope: "Task scope"})[key] || key);
      const select = element("select"); select.id = `reliability-${key}`;
      const all = element("option", "", `All ${key}s`); all.value = ""; select.append(all);
      const values = new Set([...groups, ...nodes].map((row) => row[key] || "unknown"));
      if (filters[key]) values.add(filters[key]);
      [...values].sort().forEach((value) => { const option = element("option", "", value); option.value = value; select.append(option); });
      select.value = filters[key] || "";
      select.addEventListener("change", () => { state.reliabilityFilters[key] = select.value; renderReliability(); byId(`reliability-${key}`).focus({preventScroll: true}); });
      label.append(select); controls.append(label);
    });
    controls.append(actionButton("All metric evidence", () => { state.reliabilityMetric = ""; renderReliability(); }));
    container.append(controls);
    const matches = (row) => Object.entries(filters).every(([key, value]) => !value || (row[key] || "unknown") === value);
    const groupList = element("section"); groupList.id = "reliability-groups";
    groups.filter(matches).forEach((group) => {
      const card = element("article", "detail-card");
      card.append(element("h3", "", `${taskScopeLabel(group)} · ${group.node || "unknown"} · ${group.model || "unknown"}`));
      card.append(element("p", "panel-copy", `Execution role: ${group.role || "unknown"}`));
      reliabilityMetrics(card, group.metrics, group); reliabilityCosts(card, group.costs);
      groupList.append(card);
    });
    if (!groupList.children.length) groupList.append(element("p", "empty-copy", "No recorded groups match these filters."));
    container.append(groupList);
    const selectedMetric = state.reliabilityMetric;
    const label = element("h3", "", selectedMetric ? `${RELIABILITY_METRICS.find(([key]) => key === selectedMetric)[1]}: denominator and unknown node evidence` : "All logical nodes and their Attempts");
    label.id = "reliability-list-label"; container.append(label);
    const nodeList = element("section"); nodeList.id = "reliability-nodes";
    nodes.filter(matches).filter((node) => {
      const metric = object(node.metrics)[selectedMetric];
      return !selectedMetric || !metric || metric.denominator > 0 || metric.unknown > 0;
    }).forEach((node) => {
      const card = element("article", "detail-card reliability-node");
      card.append(actionButton(`${node.node || "unknown"} · ${node.command_id || "unknown command"}`, () => openLocalDetail("reliability_node", node), "record-title"));
      card.append(element("p", "panel-copy", `${taskScopeLabel(node)} · execution role ${node.role || "unknown"} · ${node.model || "unknown"} · ${node.status || "unknown"} · ${list(node.attempts).length} recorded Attempts`));
      if (selectedMetric) card.append(element("p", "panel-copy", reliabilityMetricText(object(node.metrics)[selectedMetric])));
      nodeList.append(card);
    });
    if (!nodeList.children.length) nodeList.append(element("p", "empty-copy", "No recorded nodes match this metric and filter. Excluded nodes do not enter its denominator."));
    container.append(nodeList);
  }

  function renderReliabilityNode(parent, node) {
    structuredDetail(parent, node, ["attempts", "metrics", "costs"]);
    reliabilityCosts(parent, node.costs);
    list(node.attempts).forEach((attempt) => {
      const card = element("article", "detail-card");
      card.append(element("h4", "", `${attempt.attempt_id} · ${attempt.terminal_reason || "OPEN"}`));
      const attribution = object(attempt.attribution);
      card.append(element("p", "panel-copy", `Failure: ${attempt.failure_code || "not recorded"} · stage ${attempt.failure_stage || "unknown"} · retry class ${attempt.retry_class || "unknown"}`));
      card.append(element("p", "panel-copy", `Attribution: ${attribution.category || "unverified"} · confidence ${attribution.confidence || "unknown"} · ${attribution.reason || "Cause not established"}`));
      const observations = element("ul", "reliability-observations");
      list(attempt.observations).forEach((item) => observations.append(element("li", "panel-copy",
        [String(item.kind || "observation").replaceAll("_", " "), item.code, item.stage, item.name,
          item.count == null ? null : `count ${item.count}`, item.cause ? `cause ${item.cause}` : null].filter((value) => value != null).join(" · "))));
      if (observations.children.length) card.append(observations);
      structuredDetail(card, {corrections: attempt.corrections ?? "unavailable", trace: attempt.trace ?? "unavailable", started_at: attempt.started_at, ended_at: attempt.ended_at});
      if (attempt.attempt_id) card.append(actionButton("Open Attempt / tools / corrections", () => openRemoteDetail("attempt", {attempt_id: attempt.attempt_id}), "detail-link reliability-attempt-link"));
      parent.append(card);
    });
    if (!list(node.attempts).length) parent.append(element("p", "empty-copy", "Attempt evidence unavailable."));
  }

  function workbenchQuery() {
    const parameters = {kind: state.workbenchKind, offset: state.workbenchOffset, limit: DETAIL_PAGE_SIZE};
    const role = selectedFilters().expert;
    if (role) parameters.role = role;
    if (["resources", "sessions"].includes(state.workbenchKind)) {
      const component = state.workbenchContext.component_id || selectedFilters().component;
      if (component) parameters.component_id = component;
    }
    if (state.workbenchKind === "sessions" && state.workbenchContext.session_id) {
      parameters.session_id = state.workbenchContext.session_id;
      // An explicit parent identity can belong to another component or scope.
      delete parameters.role; delete parameters.component_id;
    }
    if (state.workbenchKind === "sessions" && state.workbenchContext.task_scope) {
      parameters.task_scope = state.workbenchContext.task_scope;
      delete parameters.role; delete parameters.component_id;
    }
    return parameters;
  }

  async function fetchDetail(parameters) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const query = new URLSearchParams();
      Object.entries(parameters).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") query.set(key, String(value)); });
      const response = await window.fetch(`/api/details?${query}`, {
        method: "GET", headers: {Authorization: `Bearer ${accessToken}`},
        cache: "no-store", credentials: "same-origin", signal: controller.signal,
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Detail access was not authorized." : "Detail evidence is unavailable for this selection.");
      return await response.json();
    } finally { window.clearTimeout(timeout); }
  }

  function openWorkbench(kind, context = {}) {
    state.workbenchKind = kind;
    state.workbenchOffset = 0;
    state.workbenchData = null;
    state.workbenchContext = context;
    renderWorkbench();
    setActiveView("workbench");
  }

  async function loadWorkbench() {
    if (!accessToken) return;
    if (state.workbenchKind === "reliability") { renderWorkbench(); return; }
    const parameters = workbenchQuery();
    const key = JSON.stringify(parameters);
    if (state.workbenchPending?.key === key) return state.workbenchPending.promise;
    const request = ++state.workbenchRequest;
    const pending = {key, promise: fetchDetail(parameters)};
    state.workbenchPending = pending;
    try {
      const data = await pending.promise;
      if (request !== state.workbenchRequest) return;
      const changed = JSON.stringify(state.workbenchData) !== JSON.stringify(data);
      state.workbenchData = data;
      if (changed && state.activeView === "workbench" && !document.hidden) renderWorkbench();
    } catch (error) {
      if (request === state.workbenchRequest) setText("workbench-status", `${error.message} Retained rows may be stale.`);
    } finally { if (state.workbenchPending === pending) state.workbenchPending = null; }
  }

  function actionButton(title, action, className = "detail-link") {
    const button = element("button", className, title);
    button.type = "button";
    button.addEventListener("click", action);
    return button;
  }

  function safeExternalLink(parent, label, value) {
    if (typeof value !== "string") return;
    let url;
    try { url = new URL(value); } catch (_error) { return; }
    if (!["https:", "http:"].includes(url.protocol)) return;
    const link = element("a", "detail-link", label);
    link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
    parent.append(link);
  }

  function memoryLabel(value) {
    return typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString()} MiB` : "unavailable";
  }

  function resourceMetric(parent, label, value, className = "") {
    const metric = element("div", `resource-metric ${className}`);
    metric.append(element("dt", "", label), element("dd", "", value));
    parent.append(metric);
  }

  function renderResourceTrace(parent, experiment, includeRaw = false) {
    const estimate = object(experiment.estimate);
    const context = element("p", "panel-copy", `${experiment.role || "Expert unavailable"} · ${experiment.component_id || "Component unavailable"}`);
    parent.append(context);
    if (estimate.basis) parent.append(element("p", "resource-basis", `Estimate basis: ${estimate.basis}`));
    const attempts = list(experiment.attempts);
    if (!attempts.length) {
      parent.append(element("p", "empty-copy", `Requested estimate: ${memoryLabel(estimate.peak_memory_mib)}. No Process Attempt is recorded yet.`));
    }
    attempts.forEach((attempt) => {
      const process = element("section", "resource-process");
      const heading = element("div", "resource-process-heading");
      heading.append(element("h4", "", `Process · ${attempt.attempt_id}`), element("span", `counter ${statusClass(attempt.state)}`, `${attempt.state || "unavailable"} · ${attempt.terminal_reason || "terminal result unavailable"}`));
      process.append(heading);
      const measured = object(attempt.measured);
      const metrics = element("dl", "resource-metrics");
      resourceMetric(metrics, "Requested estimate", memoryLabel(estimate.peak_memory_mib));
      resourceMetric(metrics, "Admitted workload", memoryLabel(attempt.admitted));
      const qualifiedPeak = measured.measurement_status === "complete" && typeof measured.sample_peak_bytes === "number"
        ? memoryLabel(Math.ceil(measured.sample_peak_bytes / 1048576)) : "unknown";
      resourceMetric(metrics, "Qualified measurement", qualifiedPeak, qualifiedPeak === "unknown" ? "resource-unknown" : "");
      process.append(metrics);
      const grant = object(attempt.grant);
      const granted = list(grant.gpu_uuids).length ? grant.gpu_uuids : grant.gpu_uuid ? [grant.gpu_uuid] : [];
      process.append(element("p", "resource-device", `Granted GPU UUID: ${granted.join(" · ") || (grant.kind === "CPU" ? "CPU only" : "unavailable")}`));
      const devices = list(attempt.usage);
      devices.forEach((device) => {
        const owned = object(device.owned_memory);
        const samples = element("dl", "resource-metrics resource-samples");
        if (devices.length > 1) process.append(element("p", "resource-device", `Observed device: ${device.gpu_uuid}`));
        resourceMetric(samples, "Own sampled peak", memoryLabel(owned.sampled_peak_memory_mib));
        resourceMetric(samples, "Samples / unknown", `${displayValue(owned.sample_count)} / ${displayValue(owned.unknown_sample_count)}`, owned.unknown_sample_count === 0 ? "" : "resource-unknown");
        resourceMetric(samples, "Coverage", owned.coverage || "unknown", owned.coverage === "complete" ? "" : "resource-unknown");
        process.append(samples);
      });
      if (!devices.length) process.append(element("p", "panel-copy", "Per-device sampled peak, sample count and coverage are unavailable."));
      parent.append(process);
    });
    const sources = element("section", "resource-sources");
    sources.append(element("h4", "", "Used by later estimates"));
    const next = list(experiment.next_estimate_sources);
    next.forEach((source) => {
      const link = actionButton(source.experiment_id, () => openRemoteDetail("resources", {experiment_id: source.experiment_id}), "detail-link resource-next-link");
      sources.append(link, element("p", "panel-copy", `Measurement source: ${list(source.measurement_attempt_ids).join(", ") || "unavailable"}`));
    });
    if (!next.length) sources.append(element("p", "panel-copy", "No later experiment cites these measurements yet."));
    parent.append(sources);
    const inputSources = list(estimate.measurement_attempt_ids);
    if (inputSources.length) {
      const references = element("p", "panel-copy", `This estimate cites: ${inputSources.join(", ")}`);
      parent.append(references);
    }
    if (includeRaw) {
      parent.append(element("p", "panel-copy", "Owned sampled peaks remain separate from qualified measurements. Unknown samples prevent a complete measurement; device occupancy is not attributed experiment memory."));
      const raw = element("details", "resource-raw");
      raw.append(element("summary", "", "Raw accepted resource evidence"), element("pre", "detail-code", displayValue(experiment)));
      parent.append(raw);
    }
  }

  function renderDownloadEvidence(parent, paper) {
    const evidence = element("details", "download-evidence");
    const attempts = list(paper.download_attempts);
    evidence.append(element("summary", "", paper.download_coverage === "recorded"
      ? `Download evidence · ${attempts.length} recorded Attempt${attempts.length === 1 ? "" : "s"}`
      : "Download history unavailable · older evidence is unknown"));
    attempts.forEach((attempt) => {
      const row = element("article", "detail-card");
      row.append(element("h4", "", `${attempt.role || "Role unavailable"} · ${attempt.attempt_id}`),
        element("p", "", `Recorded disposition: ${attempt.retrieval_disposition || "unavailable"}`));
      if (attempt.retrieval_disposition === "PDF_UNAVAILABLE" && list(attempt.events).some((event) => event.outcome === "EXTRACTION_FAILURE")) {
        row.append(element("p", "panel-copy", "PDF downloaded, but evidence extraction failed. This paper has no accepted full-text evidence."));
      }
      if (list(attempt.events).some((event) => ["landing_uri", "source_uri"].includes(event.stage) && event.outcome === "VALIDATION_FAILURE")) {
        row.append(element("p", "panel-copy", "Source address rejected. Metadata retained; this Attempt supplied no full-text evidence for this paper."));
      }
      const events = element("ul", "evidence-list");
      list(attempt.events).forEach((event) => events.append(element("li", "",
        [event.stage, event.outcome, event.source_host, event.failure_code, event.http_status ? `HTTP ${event.http_status}` : null].filter(Boolean).join(" · "))));
      if (!list(attempt.events).length) events.append(element("li", "", "No download steps retained for this Attempt."));
      row.append(events);
      if (attempt.attempt_id) row.append(actionButton("Inspect source Attempt", () => openRemoteDetail("attempt", {attempt_id: attempt.attempt_id})));
      evidence.append(row);
    });
    parent.append(evidence);
  }

  function paperSourceLink(parent, label, value, paper) {
    let host;
    try { host = new URL(value).hostname.toLowerCase().replace(/\.$/, ""); } catch (_) { return; }
    const rejected = list(paper.download_attempts).some((attempt) => list(attempt.events).some((event) =>
      ["landing_uri", "source_uri"].includes(event.stage) && event.outcome === "VALIDATION_FAILURE" && event.source_host === host));
    if (rejected) parent.append(element("p", "panel-copy", "Source link withheld: this host has a recorded address rejection. See download history."));
    else safeExternalLink(parent, label, value);
  }

  function detailCard(kind, item) {
    const card = element("article", "detail-card");
    const grid = element("dl", "selection-grid");
    const field = (name, value) => appendDefinition(grid, name, displayValue(value));
    if (kind === "skills") {
      card.append(actionButton(item.name || "Unnamed skill", () => openRemoteDetail("skill", {role: item.role, name: item.name, commit: item.commit}), "record-title"));
      field("expert", item.role); field("accepted / bound version", item.commit);
      field("current accepted / bound", item.current); field("description", item.description);
      field("request bindings", list(item.bindings).length);
      card.append(element("p", "panel-copy", "Publication and request binding do not prove a native Skill was loaded. Open adoption evidence for actual loads."));
    } else if (kind === "literature") {
      card.append(actionButton(item.title || item.bibliographic_identity || "Literature record", () => openLocalDetail("literature", item), "record-title"));
      field("identity", item.bibliographic_identity || item.identity); field("experts", item.roles);
      field("year / venue", `${displayValue(item.year || item.publication_year)} / ${displayValue(item.venue)}`);
      field("PDF evidence", item.retrieval_disposition ?? item.pdf_available ?? item.pdf_status ?? "unavailable");
      if (item.pdf_available_roles) field("PDF available to", item.pdf_available_roles);
      field("accepted reviews", item.review_attempt_ids || item.accepted_review_attempt_ids || "unavailable");
      paperSourceLink(card, "Paper / DOI ↗", item.landing_uri || item.url || item.doi_url || (item.doi ? `https://doi.org/${item.doi}` : null), item);
      paperSourceLink(card, "PDF / retrieval source ↗", item.source_uri || item.pdf_url, item);
      renderDownloadEvidence(card, item);
    } else if (kind === "sessions") {
      card.append(element("h3", "", item.session_id || "Session identity unavailable"));
      field("task ownership", taskScopeLabel(item)); field("execution role", item.role); field("component", item.component_id);
      field("scope / chain", `${displayValue(item.scope)} / ${displayValue(item.chain)}`);
      field("cycle / branch", `${displayValue(item.cycle)} / ${displayValue(item.branch)}`);
      field("observed launch modes", list(item.launch_modes).join(" → ") || "unknown");
      card.append(grid);
      if (item.parent_session_id) card.append(actionButton(`Parent session: ${item.parent_session_id}`,
        () => openWorkbench("sessions", {session_id: item.parent_session_id}), "detail-link session-parent"));
      else card.append(element("p", "panel-copy", "Parent session: not recorded."));
      list(item.attempts).forEach((attempt) => {
        const button = actionButton("", () => openRemoteDetail("attempt", {attempt_id: attempt.attempt_id}), "session-attempt");
        const mode = ["new", "fork", "resume"].includes(attempt.launch_mode) ? attempt.launch_mode : "unknown";
        button.append(element("span", "session-attempt-name", attempt.command || attempt.phase || "Attempt"),
          element("span", "counter session-launch", mode), element("span", "panel-copy", `${attempt.state} · ${attempt.attempt_id}`));
        card.append(button);
      });
      if (!list(item.attempts).length) card.append(element("p", "panel-copy", "No attempts in this session page."));
      return card;
    } else if (kind === "interactions") {
      card.append(actionButton(`${item.kind} · ${item.interaction_id || item.command_id || "interaction"}`, () => openLocalDetail("interaction", item), "record-title"));
      field("state", item.state); field("participants", item.participants);
      if (item.kind === "leader") {
        field("mode", item.mode === "experience_sharing" ? "Experience sharing · peers continue independently" : "Retained exclusive round");
        if (item.guidance) {
          field("advice author", item.guidance.author); field("advice", item.guidance.summary);
          field("supporting inputs", item.guidance.supporting_refs);
          field("provided to proposals", item.provided_to);
          card.append(element("p", "panel-copy", "Provided means included in the recorded task input. It does not establish adoption, understanding or scientific validation."));
        }
      }
      field("verdict", item.verdict); field("source Attempts", item.source_attempt_ids);
      card.append(element("p", "panel-copy", "Aborted coordination is closed control work; it does not create a scientific refutation."));
    } else if (kind === "resources") {
      card.append(actionButton(item.experiment_id, () => openLocalDetail("resource", item), "record-title"));
      renderResourceTrace(card, item);
      card.append(actionButton("Open experiment logs", () => { setActiveView("activity"); openExperimentLog(item.experiment_id); }));
    }
    card.append(grid);
    return card;
  }

  function renderWorkbench() {
    const kind = state.workbenchKind;
    const data = object(state.workbenchData);
    byId("workbench-list").classList.toggle("execution-reliability", kind === "reliability");
    setText("workbench-title", WORKBENCH_TITLES[kind]);
    document.querySelectorAll("[data-workbench]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.workbench === kind)));
    if (kind === "reliability") { renderReliability(); return; }
    const filters = selectedFilters();
    const context = state.workbenchContext;
    const scope = kind === "sessions" && context.task_scope ? `${taskScopeLabel(context)} tasks; expert/component filters retained for other views`
      : kind === "sessions" && context.session_id ? `Exact session ${context.session_id}; expert/component filters retained for other views`
      : `${filters.expert || "All experts"}${context.component_id ? ` · component ${context.component_id}` : ""}`;
    setText("workbench-scope", `${scope}. Search narrows the current page; totals count the complete run.`);
    const all = list(data[WORKBENCH_KEYS[kind]] || data.items);
    const items = all.filter((item) => !filters.search || searchable(item).includes(filters.search));
    const container = byId("workbench-list");
    // The evidence payload is already bounded by the server; do not rebuild unchanged rows on polls.
    const pool = object(data.shared_pool);
    const renderedKey = JSON.stringify([kind, items, filters.expert, filters.search, pool.devices, pool.available, pool.reason]);
    if (container.dataset.renderedKey !== renderedKey) {
      container.dataset.renderedKey = renderedKey;
      container.replaceChildren();
      if (kind === "resources" && pool.available === true) {
        const capacity = element("article", "detail-card pool-capacity");
        capacity.append(element("h3", "", "Authorized shared capacity"), element("p", "panel-copy", "Aggregate device capacity from the explicitly configured pool. No other run's private research is exposed."));
        list(pool.devices).forEach((device) => {
          const row = element("div", "resource-trace");
          const grid = element("dl", "selection-grid");
          appendDefinition(grid, "GPU", device.gpu_uuid);
          appendDefinition(grid, "free / allocatable MiB", `${displayValue(device.free_memory_mib)} / ${displayValue(device.allocatable_memory_mib)}`);
          appendDefinition(grid, "outstanding / reserve MiB", `${displayValue(device.outstanding_memory_mib)} / ${displayValue(device.reserve_memory_mib)}`);
          appendDefinition(grid, "active / unknown attribution", `${displayValue(device.active_commitments)} / ${displayValue(device.unknown_attribution_commitments)}`);
          row.append(grid); capacity.append(row);
        });
        if (pool.truncated) capacity.append(element("p", "panel-copy", "Device list truncated at the observation limit."));
        container.append(capacity);
      }
      items.forEach((item) => container.append(detailCard(kind, item)));
      if (!items.length) container.append(element("p", "empty-copy", state.workbenchData ? "No matching records in this page. Missing retained evidence remains unavailable." : "Loading selected evidence…"));
    }
    const available = data.available === false ? "unavailable" : data.coverage || "read-only evidence";
    let note = `${available} · ${items.length} matching rows on this page.`;
    if (data.unknown_versions) note += ` ${data.unknown_versions} plugin versions unavailable.`;
    if (kind === "resources") note += ` Shared pool: ${pool.available ? `observed ${formatTimestamp(pool.sampled_at)}` : pool.reason || "unavailable; no live pool observation"}.`;
    setText("workbench-status", note);
    const total = data.total;
    setText("workbench-page", `${all.length ? state.workbenchOffset + 1 : 0}–${state.workbenchOffset + all.length} of ${displayValue(total)} records`);
    byId("workbench-previous").disabled = state.workbenchOffset === 0;
    byId("workbench-next").disabled = typeof total !== "number" || state.workbenchOffset + DETAIL_PAGE_SIZE >= total;
  }

  function setDrawerMode(mode) {
    state.drawerMode = mode;
    byId("drawer-evidence-content").hidden = mode !== "evidence";
    byId("research-detail").hidden = mode !== "detail";
    byId("drawer-evidence-tab").setAttribute("aria-pressed", String(mode === "evidence"));
    byId("drawer-detail-tab").setAttribute("aria-pressed", String(mode === "detail"));
    setText("drawer-title", mode === "evidence" ? "Attempts & evidence" : "Research detail");
  }

  function openLocalDetail(kind, data) {
    ++state.detailRequest;
    state.detail = {kind, data}; state.detailTab = "overview";
    renderResearchDetail(); setDrawerMode("detail"); openDrawer();
  }

  async function openRemoteDetail(kind, parameters, refresh = false) {
    const request = ++state.detailRequest;
    if (!refresh) { state.detail = {kind, parameters, data: null}; state.detailTab = "overview"; }
    setDrawerMode("detail");
    if (!refresh) { renderResearchDetail(); openDrawer(); }
    setText("research-detail-status", "Reading run-bound evidence…");
    try {
      let data = await fetchDetail({kind, ...parameters, limit: DETAIL_PAGE_SIZE});
      if (request !== state.detailRequest) return;
      if (kind === "resources") {
        data = list(data.experiments)[0];
        if (!data) throw new Error("Resource evidence is unavailable for this experiment.");
      }
      state.detail = {kind: kind === "resources" ? "resource" : kind, requestKind: kind, parameters, data};
      const top = drawer.scrollTop;
      renderResearchDetail();
      if (refresh) drawer.scrollTop = top;
    } catch (error) {
      if (request === state.detailRequest) setText("research-detail-status", error.message);
    }
  }

  function structuredDetail(parent, record, omitted = []) {
    const grid = element("dl", "selection-grid");
    Object.entries(object(record)).filter(([key]) => !omitted.includes(key)).forEach(([key, value]) => {
      const term = element("dt", "", key.replaceAll("_", " "));
      const definition = element("dd");
      if (typeof value === "object" && value !== null) definition.append(element("pre", "detail-code", displayValue(value)));
      else definition.textContent = displayValue(value);
      grid.append(term, definition);
    });
    parent.append(grid);
  }

  function renderResearchDetail() {
    const selected = state.detail;
    const tabs = byId("research-detail-tabs");
    const content = byId("research-detail-content");
    const previousText = content.querySelector(".skill-body, .skill-diff");
    const textScroll = previousText && content.dataset.detailTab === state.detailTab ? previousText.scrollTop : 0;
    content.dataset.detailTab = state.detailTab;
    if (!selected) { content.replaceChildren(element("p", "empty-copy", "Select a skill, paper, session Attempt, interaction or experiment.")); return; }
    const data = object(selected.data);
    const kind = selected.kind;
    setText("research-detail-title", data.name || object(data.attempt).attempt_id || data.title || data.experiment_id || data.interaction_id || kind);
    const evidenceStatus = data.available === false ? kind === "attempt" && data.attempt?.failure_code
      ? "Recorded failure · session trace unavailable" : "Evidence unavailable" : "Read-only evidence";
    setText("research-detail-status", selected.data ? `${evidenceStatus}${data.scope ? ` · ${data.scope}` : ""}${data.truncated ? " · bounded response; more evidence exists" : ""}` : "Reading run-bound evidence…");
    const names = kind === "skill" ? [["overview", "Version"], ["body", "Skill content"], ["diff", "Recent diff"], ["adoption", "Adoption"], ["files", "Supporting files"]]
      : kind === "attempt" ? [["overview", "Attempt"], ["timeline", "Session / tools"], ["corrections", "Corrections"], ["execution", "Execution evidence"]]
        : kind === "interaction" ? [["overview", "Overview"], ["timeline", "Stages / verdict"]]
          : [["overview", kind === "resource" ? "Estimate → admission → measured" : "Evidence"]];
    const existingFocus = document.activeElement && document.activeElement.dataset.detailTab;
    tabs.replaceChildren();
    names.forEach(([key, label]) => {
      const button = actionButton(label, () => { state.detailTab = key; renderResearchDetail(); });
      button.dataset.detailTab = key;
      button.setAttribute("aria-pressed", String(state.detailTab === key));
      tabs.append(button);
      if (existingFocus === key) button.focus({preventScroll: true});
    });
    if (selected.parameters) tabs.append(actionButton("Refresh", () => openRemoteDetail(selected.requestKind || kind, state.detail.parameters, true)));
    const top = content.scrollTop;
    content.replaceChildren();
    if (!selected.data) return;
    if (kind === "expert") {
      renderExpertDetails(content, data.role);
    } else if (kind === "stage_accounting") {
      renderStageTokens(content, object(state.snapshot?.overview || state.product?.summary).stage_accounting);
    } else if (kind === "global_tasks") {
      renderGlobalDetails(content, data.task_scope);
    } else if (kind === "reliability_node") {
      renderReliabilityNode(content, data);
    } else if (kind === "relationship") {
      const grid = element("dl", "selection-grid");
      appendDefinition(grid, "from", data.from);
      appendDefinition(grid, "to", data.to);
      appendDefinition(grid, "relationship", relationLabel(data));
      appendDefinition(grid, "recorded relation", data.relation);
      if (data.kind === "pruning") {
        appendDefinition(grid, "meaning", "Pruning is a control decision; it does not refute the scientific claim.");
        appendDefinition(grid, "winner claim", object(data.source).claim || "—");
        appendDefinition(grid, "pruned claim", object(data.target).claim || "—");
        appendDefinition(grid, "pruned scientific assessment", object(data.target).assessment || "Not assessed");
      } else if (data.kind === "lineage") {
        appendHypothesisComparison(grid, data.target, data.source);
        appendDefinition(grid, "child claim", object(data.target).claim || "—");
        appendDefinition(grid, "child method family", object(data.target).method_family_id || "—");
        appendDefinition(grid, "child scientific assessment", object(data.target).assessment || "Not assessed");
      }
      content.append(grid);
      const raw = element("details", "raw-evidence");
      raw.append(element("summary", "", "Raw relationship evidence"));
      structuredDetail(raw, {relationship: data.recorded, source: data.source, target: data.target});
      content.append(raw);
    } else if (state.detailTab === "execution") {
      structuredDetail(content, data.trace_evidence || {coverage: "unavailable", notice: "No retained execution trace evidence."});
      list(data.related_attempts || data.attempt_links).forEach((attempt) => {
        const id = typeof attempt === "string" ? attempt : attempt.attempt_id;
        if (id) content.append(actionButton(id, () => openRemoteDetail("attempt", {attempt_id: id}), "detail-link reliability-attempt-link"));
      });
    } else if (state.detailTab === "body") {
      if (data.body_truncated) content.append(element("p", "panel-copy", "Skill body truncated at the response limit."));
      content.append(element("pre", "detail-code skill-body", data.body ?? "Skill content unavailable for this accepted version."));
    }
    else if (state.detailTab === "diff") {
      const diff = object(data.diff);
      content.append(element("p", "panel-copy", `${diff.base_commit || "No accepted base"} → ${diff.commit || data.commit || "unknown"}${diff.truncated ? " · truncated" : ""}`));
      const code = element("pre", "detail-code skill-diff");
      const text = diff.available === false ? "Diff unavailable." : diff.text ?? "No accepted evolution diff.";
      text.split("\n").forEach((line) => code.append(element("span", line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : "", `${line}\n`)));
      content.append(code);
    } else if (state.detailTab === "adoption") {
      content.append(element("p", "panel-copy", "Published commit → later request binding → native Skill load. These are separate observations; an unavailable transcript cannot prove a load."));
      const adoption = list(data.adoption);
      if (!adoption.length) content.append(element("p", "empty-copy", "No later adoption evidence recorded."));
      adoption.forEach((item) => {
        const row = element("article", "detail-card"); structuredDetail(row, item);
        if (item.attempt_id) row.append(actionButton("Inspect native session", () => openRemoteDetail("attempt", {attempt_id: item.attempt_id})));
        content.append(row);
      });
    } else if (state.detailTab === "files") {
      if (data.supporting_files_truncated) content.append(element("p", "panel-copy", "Only the first 100 supporting files are shown."));
      list(data.supporting_files).forEach((file) => {
        const section = element("details");
        section.append(element("summary", "", `${file.path} · ${file.size ?? "?"} bytes${file.truncated ? " · truncated" : ""}`),
          element("pre", "detail-code", file.text ?? "Binary or unavailable supporting asset.")); content.append(section);
      });
      if (!list(data.supporting_files).length) content.append(element("p", "empty-copy", "No supporting files projected."));
    } else if (state.detailTab === "timeline") {
      const events = kind === "interaction" ? list(data.stages) : list(data.events);
      events.forEach((event, index) => {
        const row = element("article", "timeline-event");
        row.append(element("h4", "", `${(data.offset || 0) + index + 1}. ${event.type || event.action || "event"}${event.name ? ` · ${event.name}` : ""}`));
        structuredDetail(row, event); content.append(row);
      });
      if (!events.length) content.append(element("p", "empty-copy", "No archived timeline available in this page."));
      if (kind === "attempt") {
        const offset = data.offset || 0;
        const controls = element("div", "pagination");
        if (offset > 0) controls.append(actionButton("Earlier events", () => openRemoteDetail("attempt", {...state.detail.parameters, offset: Math.max(0, offset - DETAIL_PAGE_SIZE)}, true)));
        if (typeof data.total === "number" && offset + events.length < data.total) controls.append(actionButton("Later events", () => openRemoteDetail("attempt", {...state.detail.parameters, offset: offset + DETAIL_PAGE_SIZE}, true)));
        content.append(controls);
      }
    } else if (state.detailTab === "corrections") {
      content.append(element("pre", "detail-code", displayValue(data.corrections)));
    } else if (kind === "resource") {
      renderResourceTrace(content, data, true);
    } else {
      structuredDetail(content, data, ["body", "supporting_files", "diff", "events", "corrections", "adoption", "stages", "download_attempts", "download_coverage"]);
      if (kind === "skill") {
        const versions = element("div", "version-list");
        versions.append(element("p", "panel-copy", "Accepted / bound versions (concurrent bindings may differ):"));
        list(data.versions).forEach((version) => versions.append(actionButton(
          `${compact(version.commit, 18)}${version.current ? " · current/bound" : ""}`,
          () => openRemoteDetail("skill", {role: data.role, name: data.name, commit: version.commit}))));
        content.append(versions);
      }
      if (kind === "attempt") {
        if (data.attempt?.failure_stage === "literature_retrieval") content.append(element("p", "panel-copy", "Literature preparation failed before model invocation. Missing session/tool traces do not hide the recorded failure diagnostics."));
        content.append(element("p", "panel-copy", "Normalized turns and raw provider counters may use different units. OPEN is durable state; current live subphase is unknown here."));
      }
      if (kind === "literature") {
        paperSourceLink(content, "Open paper ↗", data.landing_uri || data.url || data.doi_url, data);
        paperSourceLink(content, "Open PDF / retrieval source ↗", data.source_uri || data.pdf_url, data);
        renderDownloadEvidence(content, data);
      }
      list(data.source_attempt_ids).forEach((attempt_id) => content.append(actionButton(`Source Attempt ${attempt_id}`, () => openRemoteDetail("attempt", {attempt_id}))));
      if (data.update_attempt && data.update_attempt.attempt_id) content.append(actionButton("Inspect evolution session", () => openRemoteDetail("attempt", {attempt_id: data.update_attempt.attempt_id})));
    }
    content.scrollTop = top;
    const nextText = content.querySelector(".skill-body, .skill-diff");
    if (nextText) nextText.scrollTop = textScroll;
  }

  function renderProduct(product) {
    setText("product-status", `Observed ${product.run_id} · ${formatTimestamp(product.observed_at)}`);
    const experimentPage = state.history.experiments || product;
    const eventPage = state.history.events || product;
    setText("experiment-total", object(object(object(product.summary).totals).experiments).total ?? "unavailable");
    renderChanged("experiment-queue", [experimentPage.experiments, experimentPage.experiments_page], () => {
      const queue = byId("experiment-queue"); queue.replaceChildren();
      list(experimentPage.experiments).slice().reverse().forEach((experiment) => {
        const button = element("button", "experiment-row");
        button.type = "button";
        button.append(element("strong", "", experiment.experiment_id), element("span", "counter", experiment.state),
          element("span", "panel-copy", object(experiment.outcome).outcome || "No accepted outcome"));
        button.addEventListener("click", () => openExperimentLog(experiment.experiment_id));
        queue.append(button);
      });
      if (!queue.children.length) queue.append(element("p", "empty-copy", "No experiments submitted yet."));
      renderHistoryControls("experiments", experimentPage);
    });
    const report = object(product.final_report);
    renderChanged("report", report, () => {
      setText("final-report-source", report.attempt_id ? `As-of report from ${report.attempt_id} · ${report.verdict || "NO_ACCEPTED_VERDICT"}. ${report.stop_reason} Later stop controls remain in the event history.` : "No accepted report yet. A report is a narrative, not a new scientific verdict.");
      setText("final-report-text", report.markdown || "No accepted report yet.");
    });
    renderChanged("product-events", [eventPage.events, eventPage.events_page], () => {
      const events = byId("product-events"); events.replaceChildren();
      setText("product-event-count", list(eventPage.events).length);
      list(eventPage.events).slice().reverse().forEach((event) => {
        const row = element("article", "event-row");
        row.append(element("strong", "", event.type), element("span", "panel-copy", event.created_at),
          element("code", "", event.event_id), element("p", "", JSON.stringify(event.payload)));
        events.append(row);
      });
      renderHistoryControls("events", eventPage);
    });
  }

  function renderHistoryControls(kind, data) {
    const page = object(data[`${kind}_page`]);
    const count = list(data[kind]).length;
    setText(`${kind}-page`, `${page.offset ? "Retained older page" : "Latest page"} · ${count} of ${page.total ?? count} · offset ${page.offset || 0}`);
    byId(`${kind}-older`).disabled = page.has_more !== true;
    byId(`${kind}-latest`).disabled = !state.history[kind];
  }

  async function loadOlderHistory(kind) {
    const current = state.history[kind] || state.product;
    const page = object(current?.[`${kind}_page`]);
    if (!page.has_more) return;
    const request = ++state.historyRequests[kind];
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    byId(`${kind}-older`).disabled = true;
    try {
      const response = await window.fetch(`/api/${kind}?offset=${(page.offset || 0) + page.limit}&limit=${page.limit}`, {
        method: "GET", cache: "no-store", credentials: "same-origin",
        headers: {Authorization: `Bearer ${accessToken}`}, signal: controller.signal,
      });
      if (!response.ok) throw new Error("History page unavailable");
      const data = await response.json();
      if (request !== state.historyRequests[kind]) return;
      state.history[kind] = data;
      if (state.activeView === "activity") renderProduct(state.product);
    } catch (_error) {
      if (request === state.historyRequests[kind]) {
        setText(`${kind}-page`, "History page unavailable; retained rows remain visible.");
        byId(`${kind}-older`).disabled = false;
      }
    } finally { window.clearTimeout(timeout); }
  }

  async function poll() {
    if (state.polling || document.hidden) return;
    window.clearTimeout(state.pollTimer);
    state.polling = true;
    const controller = new AbortController();
    state.pollController = controller;
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const headers = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      const response = await window.fetch("/api/refresh", {
        method: "GET",
        cache: "no-store",
        credentials: "same-origin",
        headers,
        signal: controller.signal,
      });
      if (response.status === 401) {
        setConnection("error", accessToken ? "TOKEN REJECTED" : "TOKEN REQUIRED");
        announce("Snapshot access was not authorized. Open the startup URL containing its token fragment.");
        return;
      }
      if (!response.ok) throw new Error(`snapshot endpoint returned ${response.status}`);
      const refresh = await response.json();
      const snapshot = refresh.snapshot;
      if (!snapshot || typeof snapshot !== "object" || typeof snapshot.run_id !== "string") {
        throw new Error("snapshot response has an invalid shape");
      }
      state.lastSuccessAt = Date.now();
      state.pollFailures = 0;
      if (document.hidden) return;
      render(snapshot, refresh.activity);
      if (state.activeView === "workbench" && state.workbenchKind !== "reliability") {
        const input = JSON.stringify([snapshot.status?.run_revision, snapshot.fact_counts, snapshot.overview?.totals, workbenchQuery()]);
        if (state.renderInputs.get("workbench-fetch") !== input) {
          state.renderInputs.set("workbench-fetch", input);
          await loadWorkbench();
        }
      }
      if (document.hidden) return;
      setConnection("live", "CONNECTED");
      announce(`Observed run ${snapshot.run_id} at revision ${object(snapshot.status).run_revision ?? "unknown"}`);
    } catch (error) {
      if (document.hidden) return;
      state.pollFailures += 1;
      const message = error && error.name === "AbortError" ? "POLL TIMEOUT" : "RECONNECTING";
      setConnection(state.snapshot ? "stale" : "error", message);
      announce("Snapshot poll failed; the last successful read remains visible and may be stale.");
    } finally {
      window.clearTimeout(timeout);
      state.polling = false;
      state.pollController = null;
      if (!document.hidden) state.pollTimer = window.setTimeout(poll, Math.min(30000, POLL_INTERVAL_MS * (2 ** state.pollFailures)));
    }
  }

  function zoomAt(clientX, clientY, factor) {
    const bounds = graph.getBoundingClientRect();
    const x = clientX - bounds.left;
    const y = clientY - bounds.top;
    const oldScale = state.view.scale;
    const nextScale = Math.min(2.8, Math.max(0.2, oldScale * factor));
    const graphX = (x - state.view.x) / oldScale;
    const graphY = (y - state.view.y) / oldScale;
    state.view.scale = nextScale;
    state.view.x = x - graphX * nextScale;
    state.view.y = y - graphY * nextScale;
    applyView();
  }

  graph.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.12 : 0.89);
  }, { passive: false });

  graph.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".graph-node, .graph-edge")) return;
    graph.setPointerCapture(event.pointerId);
    graph.classList.add("is-panning");
    state.dragging = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      x: state.view.x,
      y: state.view.y,
    };
  });

  graph.addEventListener("pointermove", (event) => {
    if (!state.dragging || state.dragging.pointerId !== event.pointerId) return;
    state.view.x = state.dragging.x + event.clientX - state.dragging.clientX;
    state.view.y = state.dragging.y + event.clientY - state.dragging.clientY;
    applyView();
  });

  function finishPan(event) {
    if (!state.dragging || state.dragging.pointerId !== event.pointerId) return;
    state.dragging = null;
    graph.classList.remove("is-panning");
  }

  graph.addEventListener("pointerup", finishPan);
  graph.addEventListener("pointercancel", finishPan);
  graph.addEventListener("dblclick", fitGraph);
  graph.addEventListener("keydown", (event) => {
    if (event.target.closest(".graph-node, .graph-edge")) return;
    const centerX = graph.getBoundingClientRect().left + graph.clientWidth / 2;
    const centerY = graph.getBoundingClientRect().top + graph.clientHeight / 2;
    if (event.key === "+" || event.key === "=") zoomAt(centerX, centerY, 1.15);
    else if (event.key === "-") zoomAt(centerX, centerY, 0.87);
    else if (event.key === "0") fitGraph();
    else if (event.key === "ArrowLeft") state.view.x += 28;
    else if (event.key === "ArrowRight") state.view.x -= 28;
    else if (event.key === "ArrowUp") state.view.y += 28;
    else if (event.key === "ArrowDown") state.view.y -= 28;
    else return;
    event.preventDefault();
    applyView();
  });

  byId("fit-graph").addEventListener("click", fitGraph);
  ["events", "experiments"].forEach((kind) => {
    byId(`${kind}-older`).addEventListener("click", () => loadOlderHistory(kind));
    byId(`${kind}-latest`).addEventListener("click", () => {
      state.historyRequests[kind] += 1;
      state.history[kind] = null;
      renderProduct(state.product);
    });
  });
  byId("log-stream").addEventListener("change", () => { if (state.selectedExperiment) openExperimentLog(state.selectedExperiment); });
  window.addEventListener("pagehide", () => { if (state.logSocket) state.logSocket.close(); });
  byId("show-components").addEventListener("click", () => setTopologyMode("component"));
  byId("show-hypotheses").addEventListener("click", () => setTopologyMode("hypothesis"));
  byId("toggle-pruning").addEventListener("click", () => {
    state.showPruning = !state.showPruning;
    renderVisibleViews();
    announce(`Pruning links ${state.showPruning ? "shown" : "hidden"}; hypothesis nodes and their statuses remain visible`);
  });
  byId("open-drawer").addEventListener("click", () => {
    setDrawerMode("evidence");
    renderDrawer();
    openDrawer();
  });
  byId("open-evidence-drawer").addEventListener("click", () => {
    setDrawerMode("evidence");
    renderDrawer();
    openDrawer();
  });
  byId("close-drawer").addEventListener("click", closeDrawer);
  byId("drawer-evidence-tab").addEventListener("click", () => setDrawerMode("evidence"));
  byId("drawer-detail-tab").addEventListener("click", () => { setDrawerMode("detail"); renderResearchDetail(); });
  document.querySelectorAll("[data-workbench]").forEach((button) => button.addEventListener("click", () => openWorkbench(button.dataset.workbench)));
  byId("refresh-workbench").addEventListener("click", () => {
    loadWorkbench();
    if (!drawer.hidden && state.detail && state.detail.parameters) openRemoteDetail(state.detail.requestKind || state.detail.kind, state.detail.parameters, true);
  });
  byId("workbench-previous").addEventListener("click", () => { state.workbenchOffset = Math.max(0, state.workbenchOffset - DETAIL_PAGE_SIZE); loadWorkbench(); });
  byId("workbench-next").addEventListener("click", () => { state.workbenchOffset += DETAIL_PAGE_SIZE; loadWorkbench(); });
  drawerBackdrop.addEventListener("click", closeDrawer);
  document.querySelectorAll("[role='tab'][data-view]").forEach((tab, index, tabs) => {
    tab.addEventListener("click", () => setActiveView(tab.dataset.view));
    tab.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      setActiveView(tabs[next].dataset.view, true);
    });
  });
  filterIds.forEach((id) => byId(id).addEventListener(id === "filter-search" ? "input" : "change", () => {
    renderFilteredViews();
    if (id !== "filter-search" && state.activeView === "workbench") {
      state.workbenchOffset = 0; state.workbenchContext = {}; loadWorkbench();
    }
  }));
  byId("clear-filters").addEventListener("click", () => {
    filterIds.forEach((id) => { byId(id).value = ""; });
    renderFilteredViews();
    if (state.activeView === "workbench") { state.workbenchOffset = 0; state.workbenchContext = {}; loadWorkbench(); }
    byId("filter-search").focus();
  });
  const humanDialog = byId("human-dialog");
  const humanDrafts = new Map();
  let humanCurrent = null;
  let humanBusy = false;
  let humanReviewed = false;
  let humanAutoOpen = fragment.get("human") === "1";
  function humanDraft() { return humanCurrent ? humanDrafts.get(humanCurrent.decision_id) : {}; }
  function humanWritable() {
    const status = object(state.snapshot?.status);
    const current = list(status.grounding_dialogue).find(item => item.decision_id === humanCurrent?.decision_id);
    return Boolean(answerToken && current?.state === "OPEN" && status.run_state === "WAITING_HUMAN"
      && (humanDraft()?.pending || status.run_revision === humanCurrent?.revision));
  }
  function humanButtons() {
    const writable = humanWritable();
    const pending = Boolean(humanDraft()?.pending);
    byId("human-answer").readOnly = humanBusy || humanReviewed || !writable;
    byId("human-select").disabled = humanBusy;
    byId("review-human").hidden = humanReviewed || !writable;
    byId("review-human").disabled = humanBusy || !byId("human-answer").value.trim();
    byId("submit-human").hidden = !humanReviewed || !writable;
    byId("submit-human").disabled = humanBusy;
    byId("edit-human").hidden = !humanReviewed || !writable || pending;
    byId("human-options").querySelectorAll("button").forEach(button => { button.disabled = humanBusy || humanReviewed || !writable; });
  }
  function selectHuman(id) {
    const status = object(state.snapshot?.status);
    const decision = list(status.grounding_dialogue).find(item => item.decision_id === id);
    if (!decision) return;
    humanCurrent = {...decision, revision: status.run_revision};
    if (!humanDrafts.has(id)) humanDrafts.set(id, {text: decision.answer || ""});
    humanReviewed = Boolean(humanDraft().pending);
    setText("human-run", state.snapshot.run_id);
    setText("human-title", `Human decision · question ${decision.ordinal}`);
    setText("human-question", decision.question);
    setText("human-rationale", decision.rationale);
    setText("human-access", answerToken ? "Operator answer access. Review and submit to continue this run."
      : "This link is read-only. In TUI press h to answer directly or choose Browser for an operator link. For standalone Web, launch with --allow-human-answers.");
    byId("human-answer").value = humanDraft().text;
    byId("human-options").replaceChildren();
    list(decision.options).forEach((option, index) => {
      const card = element("article", "");
      const button = element("button", "", `Use option ${index + 1}`);
      button.type = "button";
      button.addEventListener("click", () => {
        byId("human-answer").value = option; humanDraft().text = option; humanButtons();
        byId("human-answer").focus(); byId("human-answer").scrollIntoView({block: "center"});
      });
      card.append(element("p", "", option), button); byId("human-options").append(card);
    });
    byId("submit-human").textContent = humanDraft().pending ? "Retry same answer" : "Submit answer";
    setText("human-status", humanDraft().pending ? "Previous delivery was not confirmed. Retry preserves the exact answer."
      : "Choose a suggestion or write your own answer, then review. Nothing is sent until you submit.");
    renderHumanDecisions(state.snapshot);
    humanButtons();
  }
  function openHuman(id) {
    if (!state.snapshot) return;
    const decisions = list(state.snapshot.status?.grounding_dialogue);
    if (!decisions.length) return;
    const chosen = id || decisions.find(item => item.state === "OPEN")?.decision_id || decisions[decisions.length - 1].decision_id;
    const selector = byId("human-select");
    selector.replaceChildren();
    decisions.forEach(item => {
      const option = element("option", "", `Question ${item.ordinal} · ${item.state === "OPEN" ? "Pending" : item.resolution || "Answered"}`);
      option.value = item.decision_id; selector.append(option);
    });
    selector.value = chosen;
    selectHuman(chosen);
    humanDialog.showModal();
  }
  function renderHumanDecisions(snapshot) {
    const status = object(snapshot.status);
    const decisions = list(status.grounding_dialogue);
    const pending = decisions.filter(item => item.state === "OPEN");
    const selector = byId("human-select");
    for (const item of decisions) {
      let option = Array.from(selector.options).find(option => option.value === item.decision_id);
      if (!option) { option = document.createElement("option"); option.value = item.decision_id; selector.append(option); }
      option.textContent = `Question ${item.ordinal} · ${item.state === "OPEN" ? "Pending" : item.resolution || "Answered"}`;
    }
    byId("human-pending").hidden = !pending.length;
    if (pending.length) {
      setText("human-pending-title", `${pending.length} decision pending · waiting for you`);
      setText("human-pending-question", compact(pending[0].question, 240));
    }
    if (humanCurrent) {
      const selected = decisions.find(item => item.decision_id === humanCurrent.decision_id);
      const resolved = !selected || selected.state !== "OPEN";
      byId("human-recorded").hidden = !resolved;
      if (resolved) {
        setText("human-recorded", `Recorded answer: ${selected?.answer || selected?.resolution || "unavailable"}`);
        humanDraft().pending = null;
        setText("human-status", "This decision is no longer pending. View its recorded answer or select another question.");
      } else if (humanCurrent.revision >= 0 && status.run_revision !== humanCurrent.revision && !humanDraft().pending) {
        setText("human-status", "The run changed. Close and reopen to review the current decision; your draft is retained.");
      }
      humanButtons();
    }
    if (humanAutoOpen && decisions.length) { humanAutoOpen = false; openHuman(); }
  }
  byId("open-human").addEventListener("click", () => openHuman());
  byId("human-select").addEventListener("change", event => selectHuman(event.target.value));
  byId("human-answer").addEventListener("input", () => { humanDraft().text = byId("human-answer").value; humanButtons(); });
  byId("close-human").addEventListener("click", () => { if (!humanBusy) humanDialog.close(); });
  humanDialog.addEventListener("cancel", event => { if (humanBusy) event.preventDefault(); });
  byId("review-human").addEventListener("click", () => {
    if (!humanWritable() || !byId("human-answer").value.trim()) return;
    humanReviewed = true; humanButtons();
    byId("human-answer").scrollIntoView({block: "center"});
    setText("human-status", "Review the answer above. Submit answer records it and continues this run; Edit returns to the draft.");
  });
  byId("edit-human").addEventListener("click", () => { humanReviewed = false; humanButtons(); });
  byId("submit-human").addEventListener("click", async () => {
    if (!humanWritable() || !humanReviewed || humanBusy) return;
    const draft = humanDraft();
    if (!draft.pending) draft.pending = {decision_id: humanCurrent.decision_id, answer: draft.text,
      expected_revision: humanCurrent.revision, request_id: `human-ui-${window.crypto.randomUUID()}`};
    humanBusy = true; humanButtons(); setText("human-status", "Submitting to Host…");
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/human-answer", {method: "POST", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${accessToken}`, "X-Hypoloom-Answer-Token": answerToken, "Content-Type": "application/json"},
        body: JSON.stringify(draft.pending)});
      const reply = await response.json();
      if (response.ok && reply.status === "accepted") {
        draft.pending = null;
        humanCurrent.revision = -1;
        setText("human-status", "Answer recorded. The Host can continue this run.");
      } else if ([400, 401, 403, 409].includes(response.status)) {
        draft.pending = null;
        humanCurrent.revision = -1;
        setText("human-status", `Answer not accepted: ${object(reply.error).message || reply.message || reply.error || response.status}. Refresh the observer or reopen an operator link before reviewing again.`);
      } else {
        setText("human-status", "Host reply not confirmed. Retry the same answer or inspect recorded state.");
      }
      poll();
    } catch (_error) {
      setText("human-status", "Host reply not confirmed. Retry sends the same answer safely; nothing is automatically resent.");
    } finally {
      window.clearTimeout(timer); humanBusy = false;
      byId("submit-human").textContent = draft.pending ? "Retry same answer" : "Submit answer";
      humanButtons();
    }
  });

  let supportRequest = 0;
  let supportController = null;
  function supportOptions(format) {
    const selected = byId("support-scope").value;
    const query = new URLSearchParams({format, private: byId("support-private").checked ? "1" : "0",
      max_mib: byId("support-limit").value, scope: selected === "run" ? "run" : "failures"});
    if (selected.startsWith("attempt:")) query.set("attempt_id", selected.slice(8));
    return query;
  }
  function resetSupportPreview() {
    supportRequest += 1;
    if (supportController) supportController.abort();
    byId("support-preview").hidden = true;
    byId("download-support").disabled = true;
    byId("preview-support").disabled = false;
    setText("support-status", "Selection changed. Preview this package before downloading.");
  }
  function openSupport() {
    const scope = byId("support-scope");
    Array.from(scope.querySelectorAll("[data-attempt-option]")).forEach(option => option.remove());
    list(state.snapshot?.attempts).slice(-100).reverse().forEach(attempt => {
      const option = element("option", "", `${attempt.role || attempt.kind || "Attempt"} · ${attempt.command || attempt.phase || attempt.attempt_id} · ${attempt.terminal_reason || attempt.state || "unknown"}`);
      option.value = `attempt:${attempt.attempt_id}`;
      option.dataset.attemptOption = "true";
      scope.append(option);
    });
    resetSupportPreview();
    supportDialog.showModal();
  }
  async function requestSupport(format) {
    const request = ++supportRequest;
    if (supportController) supportController.abort();
    const controller = new AbortController();
    supportController = controller;
    const timer = window.setTimeout(() => controller.abort(), 90000);
    byId("preview-support").disabled = true;
    byId("download-support").disabled = true;
    setText("support-status", format === "zip" ? "Preparing download…" : "Reading available diagnostic evidence…");
    try {
      const response = await window.fetch(`/api/support?${supportOptions(format)}`, {
        method: "POST", headers: {Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json"},
        body: JSON.stringify(Object.fromEntries(["actual", "expected", "steps", "occurred_at"]
          .map(key => [key, byId(`support-${key}`).value]).filter(([, value]) => value))),
        cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error(response.status === 401 ? "Open the authenticated observer URL first."
        : response.status === 429 ? "Another package is being prepared. Try again shortly." : "Could not prepare this selection. Try a different scope or use the CLI.");
      if (format === "zip") {
        const blob = await response.blob();
        if (request !== supportRequest) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = byId("support-private").checked ? "hypoloom-support-private.zip" : "hypoloom-support.zip";
        document.body.append(link); link.click(); link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        setText("support-status", "Download requested. Inspect README.md and the files locally before sharing; nothing was uploaded.");
      } else {
        const preview = await response.json();
        if (request !== supportRequest) return;
        setText("support-readme", preview.readme);
        setText("support-report", JSON.stringify(preview.report, null, 2));
        setText("support-diagnostics", JSON.stringify(preview.diagnostics, null, 2));
        byId("support-preview").hidden = false;
        const report = object(preview.report);
        setText("support-status", `${report.exported_file_count ?? "?"} files · ${compactNumber(report.exported_payload_bytes)} bytes before report metadata. ${report.limited ? "Some evidence was omitted by the limits. " : ""}${object(report.diagnosis).available ? "" : "Run diagnosis unavailable; available fallback evidence is included. "}Download reads current evidence with these options.`);
      }
      byId("download-support").disabled = false;
    } catch (error) {
      if (request !== supportRequest) return;
      setText("support-status", error?.name === "AbortError" ? "Preparation timed out. Try a smaller scope or use the CLI." : error.message);
    } finally {
      window.clearTimeout(timer);
      if (request === supportRequest) byId("preview-support").disabled = false;
    }
  }
  byId("open-support").addEventListener("click", openSupport);
  byId("close-support").addEventListener("click", () => supportDialog.close());
  supportDialog.addEventListener("close", resetSupportPreview);
  ["support-scope", "support-limit", "support-private"].forEach(id => byId(id).addEventListener("change", resetSupportPreview));
  ["actual", "expected", "steps", "occurred_at"].forEach(key => byId(`support-${key}`).addEventListener("input", resetSupportPreview));
  byId("preview-support").addEventListener("click", () => requestSupport("preview"));
  byId("download-support").addEventListener("click", () => requestSupport("zip"));
  if (fragment.get("support") === "1") openSupport();

  byId("open-help").addEventListener("click", () => {
    if (typeof helpDialog.showModal === "function") helpDialog.showModal();
    else helpDialog.setAttribute("open", "");
  });
  byId("close-help").addEventListener("click", () => {
    if (typeof helpDialog.close === "function") helpDialog.close();
    else helpDialog.removeAttribute("open");
  });
  helpDialog.addEventListener("click", (event) => {
    if (event.target === helpDialog && typeof helpDialog.close === "function") helpDialog.close();
  });

  const TEXT_SIZE_STORAGE_KEY = "hypoloom.text-size";
  const TEXT_SIZES = ["87.5", "100", "112.5", "125"];
  const textSizeSelect = byId("text-size");
  function applyTextSize(value) {
    const size = TEXT_SIZES.includes(value) ? value : "112.5";
    document.documentElement.style.fontSize = `${size}%`;
    if (textSizeSelect.value !== size) textSizeSelect.value = size;
    return size;
  }
  let storedTextSize = null;
  try { storedTextSize = window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY); } catch { storedTextSize = null; }
  applyTextSize(storedTextSize);
  textSizeSelect.addEventListener("change", () => {
    const size = applyTextSize(textSizeSelect.value);
    try { window.localStorage.setItem(TEXT_SIZE_STORAGE_KEY, size); } catch { /* preference stays session-only */ }
    const label = textSizeSelect.selectedOptions.length ? textSizeSelect.selectedOptions[0].textContent : size;
    setText("screen-status", `Text size set to ${label}.`);
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !drawer.hidden) closeDrawer();
  });
  window.addEventListener("resize", () => window.requestAnimationFrame(fitGraph));

  document.addEventListener("visibilitychange", () => {
    window.clearTimeout(state.pollTimer);
    if (document.hidden) {
      if (state.pollController) state.pollController.abort();
      if (state.logSocket) { state.logSocket.close(); state.logSocket = null; }
      setConnection("stale", "PAUSED · TAB HIDDEN");
    } else {
      renderVisibleViews();
      if (state.activeView === "activity" && state.selectedExperiment) openExperimentLog(state.selectedExperiment);
      poll();
    }
  });

  window.setInterval(() => {
    if (document.hidden) return;
    if (state.lastSuccessAt && Date.now() - state.lastSuccessAt > STALE_AFTER_MS) {
      const seconds = Math.floor((Date.now() - state.lastSuccessAt) / 1000);
      setConnection("stale", `STALE ${seconds}s · RETRYING`);
    }
  }, 1000);

  if (!accessToken) {
    setConnection("error", "TOKEN REQUIRED");
  }
  setActiveView("operations");
  poll();
})();

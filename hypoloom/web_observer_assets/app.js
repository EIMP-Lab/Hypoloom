(() => {
  "use strict";

  const POLL_INTERVAL_MS = 3000;
  const REQUEST_TIMEOUT_MS = 8000;
  const STALE_AFTER_MS = POLL_INTERVAL_MS + REQUEST_TIMEOUT_MS + 4000;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const SESSION_TOKEN_KEY = "hypoloom-observer-token";
  const LANGUAGE_STORAGE_KEY = "hypoloom.language";
  let uiLanguage = "en";
  let messages = {};

  // Translate authored templates before inserting record values. Never translate
  // research prose, identifiers, log contents, commands or protocol payloads.
  function t(message, ...values) {
    const template = uiLanguage === "zh-CN" && Object.hasOwn(messages, message) ? messages[message] : message;
    return values.length ? template.replace(/\{\{(.*?)\}\}|\{(\d+)\}/g,
      (match, literal, index) => index === undefined ? `{${literal}}` : String(values[Number(index)])) : template;
  }

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
  const staticMessages = Array.from(document.querySelectorAll("[data-i18n]"), node => ({node, source: node.textContent, rendered: node.textContent}));
  const staticAttributes = Array.from(document.querySelectorAll("[title], [aria-label], [placeholder]"))
    .flatMap(node => ["title", "aria-label", "placeholder"].filter(name => node.hasAttribute(name))
      .map(name => ({node, name, source: node.getAttribute(name)})));

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
  const literatureStorageKey = `${SESSION_TOKEN_KEY}.literature`;
  let literatureToken = fragment.get("literature_token") || "";
  try {
    if (fragmentToken) {
      if (literatureToken) window.sessionStorage.setItem(literatureStorageKey, JSON.stringify({accessToken, literatureToken}));
      else window.sessionStorage.removeItem(literatureStorageKey);
    } else {
      const saved = JSON.parse(window.sessionStorage.getItem(literatureStorageKey) || "{}");
      literatureToken = saved.accessToken === accessToken ? saved.literatureToken || "" : "";
    }
  } catch (_error) { if (literatureToken) fragmentStored = false; }
  // Clear a credential-bearing fragment only after the reload-safe session copy succeeds.
  if (fragmentToken && fragmentStored) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }

  const state = {
    snapshot: null,
    lastSuccessAt: 0,
    observation: null,
    pollError: null,
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
    skillFilters: {window: "", command: ""},
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
    literatureBusy: false,
    literatureMessage: "",
    literatureDraft: new Map(),
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
    const node = byId(id);
    node.removeAttribute("data-i18n");
    node.textContent = value === undefined || value === null || value === "" ? "—" : String(value);
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
    const objective = String(object(contract.goal).objective || object(snapshot.goal).objective || t("Research objective unavailable"));
    setText("mission-title", sharedGoal(snapshot) ? t("Shared research goal") : t("Research objective"));
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
    if (seconds >= 86400) return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  function announce(message) {
    screenStatus.textContent = message;
  }

  function setConnection(kind, message) {
    connection.dataset.state = kind;
    connectionText.textContent = message;
    if (state.snapshot) renderTraffic(state.snapshot);
    providerButtons();
    if (kind !== "live" && state.snapshot) {
      setText("provider-title", t("API status unconfirmed · observation stale"));
    }
  }

  function updateConnection(force = false) {
    if (document.hidden) return;
    let kind = "live", message = t("CONNECTED");
    const observed = state.observation;
    const age = observed && Number.isFinite(observed.age_seconds)
      ? observed.age_seconds * 1000 + Date.now() - state.lastSuccessAt
      : state.lastSuccessAt ? Date.now() - state.lastSuccessAt : 0;
    if (state.pollError) {
      kind = state.snapshot && !state.pollError.startsWith("TOKEN ") ? "stale" : "error";
      message = t(state.pollError);
    } else if (observed?.state === "error") {
      kind = state.snapshot ? "stale" : "error";
      message = t("REFRESH FAILED · RETRYING");
    } else if (!state.snapshot) {
      kind = "connecting";
      message = t("PREPARING SNAPSHOT");
    } else if (age > STALE_AFTER_MS) {
      kind = "stale";
      message = t(observed?.refreshing ? "DATA {0}s OLD · UPDATING" : "DATA {0}s OLD", Math.floor(age / 1000));
    } else if (observed?.refreshing) {
      message = t("CONNECTED · UPDATING");
    }
    if (force || connection.dataset.state !== kind || connectionText.textContent !== message) setConnection(kind, message);
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
    updateSelect("filter-component", shared ? [] : list(snapshot.components).map((item) => item.component_id).sort(), t("All components"));
    updateSelect("filter-expert", list(snapshot.experts).map((item) => item.role).sort(), t("All experts"));
    updateSelect(
      "filter-kind",
      Array.from(new Set([
        ...list(snapshot.attempts).map((item) => item.kind),
        ...list(snapshot.rendezvous).map((item) => item.kind),
      ])).sort(),
      t("All kinds"),
    );
    updateSelect(
      "filter-state",
      Array.from(new Set([
        ...buildTopology(snapshot, "component").map((item) => item.state),
        ...buildTopology(snapshot, "hypothesis").map((item) => item.state),
        ...list(snapshot.rendezvous).map((item) => item.status),
        ...list(snapshot.attempts).map((item) => item.state),
      ])).sort(),
      t("All states"),
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
    byId("provider-retry").disabled = providerBusy || !providerWritable()
      || (!providerPending && state.snapshot?.status?.provider_retry?.state === "recovering");
    byId("provider-switch").disabled = providerBusy || !providerWritable() || Boolean(providerPending);
    byId("provider-settings").disabled = providerBusy || Boolean(providerPending);
    byId("provider-retry").textContent = providerPending ? t("Retry same request") : t("Try current API");
  }
  function literatureStatusText(value, terminal) {
    if (!value) return t("Literature service status unknown.");
    const labels = {ready: value.recovered_at ? t("Retrieval recovered") : t("Retrieval allowed; access unverified"),
      cooldown: t("Automatic retry scheduled"), recovering: t("Waiting for automatic retry"), waiting_operator: t("Waiting for operator")};
    const reasons = {openalex_timeout: t("Request timed out"), openalex_rate_limit: t("Rate limited"),
      openalex_auth: t("Authentication rejected"), openalex_http: t("HTTP request failed")};
    const lines = [t("OpenAlex: {0}.", terminal ? t("Run ended") : labels[value.state] || t("Status unknown"))];
    if (value.active_service === "crossref") lines[0] = t("Literature: Crossref fallback; OpenAlex recovery unconfirmed.");
    const reason = value.reason || value.primary_failure;
    if (reason) lines.push(t("Last failure: {0}{1}.", reasons[reason] || reason,
      value.http_status == null ? "" : ` · HTTP ${value.http_status}`));
    if (terminal) lines.push(t("Run ended; no further automatic retry."));
    else if (value.requires_operator || value.state === "waiting_operator") lines.push(t("Automatic retry is paused; operator action is required."));
    else if (value.state === "recovering") lines.push(t("An eligible task will retry automatically; access is not yet confirmed."));
    else if (value.next_retry_at != null) lines.push(t("Next automatic retry: {0}.", formatTimestamp(value.next_retry_at)));
    if (list(value.affected_roles).length) lines.push(t("Affected roles: {0}.", value.affected_roles.join(", ")));
    return lines.join(" ");
  }
  function renderProvider(snapshot) {
    const retry = snapshot.status?.provider_retry;
    const terminal = ["COMPLETED", "FAILED", "CANCELLED"].includes(snapshot.status?.run_state);
    const labels = {ready: t("API dispatch available"), cooldown: t("API cooling down"), waiting_operator: t("API waiting for operator"), recovering: t("Trying API recovery")};
    setText("provider-title", terminal ? t("Run ended") : retry ? labels[retry.state] || t("API status") : t("API status unknown"));
    const reasons = {auth_failed: t("Authentication rejected"), rate_limited: t("Rate limit; quota cause unconfirmed"), service_unavailable: t("Provider temporarily unavailable"), usage_window_exhausted: t("Usage window quota exhausted"), quota_exhausted: t("Quota exhausted"), subscription_expired: t("Subscription expired; renew or switch API"), access_denied: t("Access restricted; check permissions or contact the provider"), connection_configuration: t("Connection configuration unavailable")};
    setText("provider-description", terminal ? t("No API recovery is available for an ended run.")
      : !retry ? t("Live Host status is unavailable. API availability cannot be confirmed.")
      : retry.scheduled_retry ? t("API retry scheduled for {0} ({1}s remaining). Automatic retries paused; submitted experiments continue.", formatTimestamp(retry.scheduled_retry.retry_at), Math.max(0, Math.ceil(retry.scheduled_retry.retry_at - Date.now() / 1000)))
      : retry.state === "ready" ? t("Normal dispatch is allowed, subject to task and budget limits.")
      : retry.state === "recovering" ? t("One pending task may try the API. Access is not yet confirmed.")
      : t("{0}. New Agent dispatch paused; submitted experiments continue. {1}", reasons[retry.reason] || t("API unavailable"), retry.requires_operator ? t("Operator action required.") : t("Next attempt: {0}.", formatTimestamp(retry.next_retry_at))));
    setText("provider-access", providerToken
      ? t("Operator recovery access includes GPU recheck, literature and notification retry. API switch uses a Host-side 0600 JSON connection file; the model stays fixed. Never enter a key here.")
      : t("Read-only. Use TUI p for API recovery or obtain an operator API link with --allow-provider-control."));
    const blocks = snapshot.status?.resource_blocked || [];
    setText("resource-blocked", blocks.length ? t("GPU infrastructure blocked: {0}. Submitted experiments continue; affected nodes await repair and recheck.", blocks.map(b => `${b.role}: ${b.code}`).join(" · ")) : "");
    if (terminal && blocks.length) setText("resource-blocked", t("Run ended; GPU fault history is retained. No resource recovery is available."));
    const recheck = byId("resource-recheck");
    recheck.hidden = terminal || !providerToken || (!blocks.length && !resourcePending);
    recheck.disabled = resourceBusy || !providerWritable();
    recheck.textContent = resourcePending ? t("Retry same resource recheck") : t("Recheck GPU resources");
    const literature = snapshot.status?.literature_retry;
    setText("literature-status", literatureStatusText(literature, terminal));
    const notifications = snapshot.status?.notifications;
    const sinks = Object.entries(notifications?.sinks || {}).filter(([name]) => name !== "log");
    setText("notification-status", !notifications || notifications.state === "unavailable" ? t("Notification delivery status unknown; owning service unavailable.")
      : notifications.error ? t("Notification service: {0}", notifications.error)
      : !sinks.length ? t("No remote notification delivery has been observed.")
      : sinks.map(([name, h]) => t("{0}: {1} · pending {2} · last success {3}{4}{5}", name, (h.state || "unverified").replaceAll("_", " "), h.pending ?? "unknown", formatTimestamp(h.last_success_at), h.failure ? ` · ${h.failure.code} (${h.failure.phase}${h.failure.errno == null ? "" : `, errno ${h.failure.errno}`})` : "", h.next_retry_at ? t(" · next retry {0}", formatTimestamp(h.next_retry_at)) : "")
        + (name === "telegram" ? t(" · route: {0}", ({direct: t("Direct"), http_proxy: t("HTTP proxy")})[h.route] || t("Unknown route")) : "")).join(" | "));
    for (const kind of ["literature", "notification"]) {
      const button = byId(`${kind}-retry`);
      button.hidden = !providerToken || terminal || (kind === "notification" ? !notifications?.sinks?.telegram : !literature);
      button.disabled = dependencyBusy || !providerWritable();
      button.textContent = dependencyPending[kind] ? t("Retry same request") : kind === "literature" ? t("Retry literature service") : t("Retry Telegram delivery");
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
      setText("dependency-operation", result.status === "accepted" ? t("Retry requested. Recovery is confirmed only after an actual successful response.") : t("Retry not accepted; inspect current status."));
    } catch (_) { setText("dependency-operation", t("Host reply not confirmed. Retry sends the same request.")); }
    finally { clearTimeout(timer); dependencyBusy = false; renderProvider(state.snapshot); }
  }
  byId("literature-retry").addEventListener("click", () => requestDependency("literature"));
  byId("notification-retry").addEventListener("click", () => requestDependency("notification"));
  async function requestResources() {
    if (resourceBusy || !providerWritable()) return;
    resourcePending ||= {request_id: `web-resource-${crypto.randomUUID()}`};
    resourceBusy = true; byId("resource-recheck").disabled = true;
    setText("resource-operation", t("Rechecking the bound GPU pool…"));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch("/api/resource-recheck", {method: "POST", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${accessToken}`, "X-Hypoloom-Provider-Token": providerToken, "Content-Type": "application/json"}, body: JSON.stringify(resourcePending)});
      const result = await response.json();
      if (result.status === "accepted") {
        resourcePending = null; setText("resource-operation", t("GPU pool rechecked. Existing retry and task budgets remain in effect."));
      } else if (["rejected", "conflict"].includes(result.status) || [400, 403].includes(response.status)) {
        resourcePending = null; setText("resource-operation", t("Not accepted: {0}", result.error?.message || result.message || t("check operator access")));
      } else throw new Error("unconfirmed");
    } catch (_error) { setText("resource-operation", t("Host reply not confirmed. Retry sends the same resource recheck.")); }
    finally { clearTimeout(timer); resourceBusy = false; renderProvider(state.snapshot); }
  }
  byId("resource-recheck").addEventListener("click", requestResources);
  async function requestProvider(switchConnection) {
    if (providerBusy || !providerWritable()) return;
    if (!providerPending) {
      const settings = byId("provider-settings").value.trim();
      if (switchConnection && !settings.startsWith("/")) {
        setText("provider-operation", t("Select an absolute Host-side connection file path.")); return;
      }
      providerPending = {request_id: `web-provider-${crypto.randomUUID()}`,
        ...(switchConnection ? {settings_path: settings} : {})};
    }
    providerBusy = true; providerButtons();
    setText("provider-operation", t("Requesting API recovery…"));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch("/api/provider-retry", {method: "POST", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${accessToken}`, "X-Hypoloom-Provider-Token": providerToken, "Content-Type": "application/json"},
        body: JSON.stringify(providerPending)});
      const result = await response.json();
      if (result.status === "accepted") {
        providerPending = null;
        setText("provider-operation", t("Recovery requested. Awaiting a successful API response; existing results and budgets are preserved."));
      } else if (["rejected", "conflict"].includes(result.status) || [400, 403].includes(response.status)) {
        providerPending = null;
        setText("provider-operation", t("Not accepted: {0}", result.error?.message || result.message || t("check operator access and connection file")));
      } else throw new Error("unconfirmed");
    } catch (_error) {
      setText("provider-operation", t("Host reply not confirmed. Retry sends the same request; inspect current state before another operation."));
    } finally { clearTimeout(timer); providerBusy = false; providerButtons(); }
  }
  byId("provider-retry").addEventListener("click", () => requestProvider(false));
  byId("provider-switch").addEventListener("click", () => requestProvider(true));

  function preservePulseTiming(container) {
    // Reparenting even a reused light cancels its CSS animation. Carry only the
    // browser's timing across this synchronous redraw, then discard these values.
    const timings = container.getAnimations({subtree: true})
      .filter(animation => animation.animationName === "breath" && animation.currentTime !== null)
      .map(animation => ({light: animation.effect.target, time: animation.currentTime,
        start: animation.startTime, paused: animation.playState === "paused"}));
    return () => timings.forEach(({light, time, start, paused}) => {
      if (!light.isConnected) return;
      const animation = light.getAnimations().find(item => item.animationName === "breath");
      if (!animation) return;
      if (paused) {
        animation.pause();
        animation.currentTime = time;
      } else if (start !== null) animation.startTime = start;
      else animation.currentTime = time;
    });
  }

  function renderTraffic(snapshot) {
    const traffic = object(snapshot.status?.provider_traffic);
    const age = Number(snapshot.observed_at) - Number(traffic.observed_at);
    const fresh = connection.dataset.state === "live" && traffic.scope === "run"
      && !traffic.warming_up && Number.isFinite(age) && Math.abs(age) <= 30
      && object(snapshot.overview).host_observation?.availability === "responded";
    document.documentElement.dataset.rpmFresh = String(fresh);
    renderConcurrency(snapshot, fresh);
    const valid = value => Number.isInteger(value) && value >= 0;
    const describe = counts => fresh && valid(counts.rpm)
      ? t("Last 60 seconds: {0} outbound API attempts · in flight {1} · queued {2}. This run only; includes retries, not other account clients.", counts.rpm, counts.in_flight ?? "?", counts.queued ?? "?")
      : t("Live RPM unavailable; no fresh Host traffic observation.");
    const fill = (node, counts) => {
      if (!node) return;
      node.textContent = fresh && valid(counts.rpm) ? String(counts.rpm) : "—";
      node.title = describe(counts);
    };
    fill(byId("run-rpm"), traffic);
    fill(byId("grounding-rpm"), object(traffic.grounding));
    const pulse = (light, counts, aggregate) => {
      if (!light) return;
      const active = fresh && valid(counts.rpm) && counts.rpm > 0 && light.classList.contains("host-confirmed");
      light.classList.toggle("rpm-active", active);
      if (active) {
        const period = (.8 + 1.2 / Math.sqrt(counts.rpm)) * (aggregate ? 1.35 : 1);
        const duration = `${period}s`;
        if (light.style.getPropertyValue("--rpm-period") !== duration) {
          // Keep the browser's current phase when RPM changes; ordinary polls
          // leave its animation clock alone instead of seeking on every refresh.
          const animation = light.getAnimations().find(item => item.animationName === "breath");
          const progress = animation?.effect.getComputedTiming().progress;
          light.style.setProperty("--rpm-period", duration);
          if (typeof progress === "number") animation.currentTime = progress * period * 1000;
        }
      }
      light.title = `${light.dataset.workTitle || ""}\n${describe(counts)} ${t("Pulse frequency follows RPM; it is not a heartbeat or token speed.")}`;
    };
    pulse(byId("grounding-pulse"), object(traffic.grounding), true);
    const grounding = object(snapshot.overview?.grounding);
    const counts = object(traffic.grounding);
    const groundingState = grounding.state === "preparing" && connection.dataset.state === 'live' && snapshot.status?.provider_retry?.state === 'cooldown' ? 'COOLDOWN'
      : grounding.state === "active" && fresh && counts.queued > 0 && !counts.in_flight ? "QUEUED"
      : ({active: "RUN", completed: "DONE", waiting_human: "WAIT", failed: "FAIL", cancelled: "CANCELLED", preparing: "PREP", not_started: "IDLE"}[grounding.state] || "UNKNOWN");
    if (byId('grounding-state')) setText("grounding-state", groundingState === 'RUN' && (connection.dataset.state !== 'live' || object(snapshot.overview).host_observation?.availability !== 'responded') ? 'OPEN?' : groundingState);
    document.querySelectorAll('.agent-tile[data-attempt]').forEach(tile => {
      if (tile.dataset.kind !== 'AGENT') return;
      const own = traffic.by_attempt ? object(traffic.by_attempt[tile.dataset.attempt] || {rpm:0,in_flight:0,queued:0}) : {};
      const metric = tile.querySelector('.agent-tile-metric');
      metric.title = `${t("Request rate: {0} RPM", fresh && valid(own.rpm) ? own.rpm : "—")}\n${describe(own)}`;
      pulse(tile.querySelector('.agent-tile-light'), own, false);
      tile.title = `${tile.dataset.workTitle || ''}\n${metric.title}`;
      tile.setAttribute('aria-label', `${tile.dataset.workLabel || ''} · ${metric.title}`);
    });
    document.querySelectorAll(".expert-preview[data-expert-role]").forEach(card => {
      const counts = object(object(traffic.by_expert)[card.dataset.expertRole] || {rpm: 0, in_flight: 0, queued: 0});
      fill(card.querySelector(".expert-rpm-value"), counts);
      pulse(card.querySelector(".breath-dot"), counts, true);
    });
  }

  function renderHud(snapshot) {
    renderTraffic(snapshot);
    renderProvider(snapshot);
    renderHumanDecisions(snapshot);
    const status = object(snapshot.status);
    const oracle = object(snapshot.oracle);
    setText("run-id", snapshot.run_id);
    const overview = object(snapshot.overview || state.product?.summary);
    const elapsed = overview.elapsed_seconds;
    setText("run-elapsed", Number.isFinite(elapsed) && elapsed >= 0 ? formatDuration(0, elapsed) : "—");
    byId("run-elapsed").title = t("Elapsed wall time through the latest observation, including waiting. Stops when the run ends.");
    const accounting = object(overview.accounting);
    const reported = accounting.reported_tokens;
    const unsettled = accounting.observed_unsettled_tokens;
    const hasReported = Number.isFinite(reported) && reported >= 0;
    const hasUnsettled = Number.isFinite(unsettled) && unsettled >= 0;
    const hasTokens = hasReported || hasUnsettled;
    // The summary owns these disjoint totals; never add reserves or retry subsets.
    setText("run-tokens", hasTokens ? `≥${compactNumber((hasReported ? reported : 0) + (hasUnsettled ? unsettled : 0), true)}` : "—");
    byId("run-tokens").title = hasTokens
      ? t("Observed token lower bound: {0} reported + {1} from unsettled calls. Reservations are excluded; missing usage remains unknown.", hasReported ? reported : "?", hasUnsettled ? unsettled : "?")
      : t("Token usage observation unavailable.");
    const hostObservation = object(object(state.snapshot && state.snapshot.overview).host_observation);
    const active = !["COMPLETED", "CANCELLED", "FAILED"].includes(status.run_state);
    const hostAbsent = active && hostObservation.availability === "absent";
    const literaturePending = object(status.literature_access).pending;
    const failures = list(snapshot.attempts).filter(item => item.operational_status === "recovery_exhausted");
    const codes = [...new Set(failures.map(item => item.operational_reason || "agent_final_failure"))];
    const warning = failures.length ? ` · ⚠ ${codes.includes("error_max_budget_usd") ? t("BUDGET LIMIT") : t("AGENT FINAL FAILURE")} (${failures.length}): ${codes.slice(-3).join(", ")}` : "";
    setText("run-state", `${status.run_state}${hostAbsent ? t(" · HOST ABSENT (orphaned)") : ""}${active && status.dispatch_error ? t(" · DISPATCH PAUSED (Host error)") : ""}${warning}${literaturePending ? t(" · Institutional full text: {0} pending", literaturePending) : ""}`);
    byId("run-state").classList.toggle("blocked", failures.length > 0 || Boolean(active && status.dispatch_error));
    setText("goal-verdict", status.goal_verdict || "PENDING");
    setText(
      "required-assessed",
      `${status.required_assessment_count ?? "?"}/${status.required_component_count ?? "?"}`,
    );
    setText("run-revision", status.run_revision);
    setText("observed-at", formatTimestamp(snapshot.observed_at));
    const runOpen = status.open_attempts;
    const rootOpen = snapshot.durable_open_attempts_state_root;
    setText("open-attempts", t("{0} run / {1} root", runOpen ?? "?", rootOpen ?? "?"));
    const audit = object(oracle.observation);
    setText("oracle-state", audit.state === "pending" ? t("AUDIT PENDING") : audit.state === "error" ? t("AUDIT UNAVAILABLE")
      : oracle.ok === true ? t("LAST CLEAN") : oracle.ok === false ? t("LAST ISSUES") : "UNKNOWN");
    byId("oracle-state").title = audit.audited_at ? t("Historical audit from {0}{1}", formatTimestamp(audit.audited_at), audit.refreshing ? t("; refresh pending") : "") : t("No completed audit time available");
  }

  function renderMission(snapshot) {
    const status = object(snapshot.status);
    const goalPayload = object(snapshot.goal);
    const contract = object(goalPayload.research_contract);
    renderObjective(snapshot);
    const schema = contract.schema_version === undefined ? "?" : contract.schema_version;
    byId("mission-meta").textContent =
      t("{0} · contract schema {1} · ", snapshot.status_source || t("offline projection"), schema) +
      (answerToken ? t("operator answers require review and explicit submission") : t("read-only observation; operator link required to submit answers"));
    const dialogue = list(status.grounding_dialogue);
    byId("grounding-dialogue-panel").hidden = dialogue.length === 0;
    const history = byId("grounding-dialogue-list");
    history.replaceChildren();
    dialogue.forEach((item) => {
      const card = element("article", "panel");
      card.append(element("h3", "", t("Round {0} · {1}", item.ordinal, item.state)),
        element("p", "", item.question), element("p", "", item.rationale),
        element("p", "", t("Decision: {0}", item.decision_id)));
      if (list(item.options).length) card.append(element("p", "", t("Suggestions: {0}", item.options.join(" / "))));
      if (item.answer !== null) card.append(element("p", "", t("Answer: {0}", item.answer)));
      else card.append(element("p", "", item.state === "OPEN" ? t("Waiting for your answer; no model invocation remains open.") : t("Closed: {0}", item.resolution)));
      const review = element("button", "", item.state === "OPEN" ? t("Review and answer") : t("View decision"));
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
    panel.append(element("h2", "", t("Shared acceptance")),
      element("p", "prose", acceptance.summary || t("Accepted criteria unavailable.")),
      element("p", "", acceptance.rule === "ALL" ? t("All conditions must hold (ALL)")
        : acceptance.rule === "ANY" ? t("Any condition may hold (ANY)") : t("Acceptance rule unavailable")),
      element("p", "", t("Grounding source Attempt: {0}", common.source_attempt_id)),
      element("p", "panel-copy", t("Accepted declaration and published observations only. The recorded goal verdict remains authoritative; expert values are not merged or compared here.")));
    const criteria = element("ul", "acceptance-conditions");
    const operators = {GT: ">", GE: "≥", LT: "<", LE: "≤", EQ: "=", IS_TRUE: t("must be true")};
    list(acceptance.criteria).forEach((criterion) => criteria.append(element("li", "",
      `${criterion.name} ${operators[criterion.operator] || criterion.operator}${criterion.operator === "IS_TRUE" ? "" : ` ${criterion.target}`}`)));
    panel.append(criteria, element("h3", "", t("Required validity checks")));
    const validity = element("ul", "acceptance-validity");
    list(acceptance.validity_checks).forEach((name) => validity.append(element("li", "", t("{0} must be true", name))));
    if (!list(acceptance.validity_checks).length) validity.append(element("li", "", t("No separate validity checks declared.")));
    panel.append(validity, rawDetails(t("Technical source · declaration and parser"), acceptance));
    renderMetricTrends(panel, common, snapshot);
    const outcomes = list(common.outcomes);
    if (!outcomes.length) panel.append(element("p", "", t("No published experiment outcomes yet.")));
    const records = element("details", "acceptance-records");
    records.append(element("summary", "", t("Published experiment records · {0}", outcomes.length)));
    outcomes.forEach((item) => {
      const card = element("article", "panel");
      card.append(element("h3", "", `${item.experiment_id} · ${item.scope || t("scope unavailable")}`),
        element("p", "", t("Component: {0} · candidate: {1}", item.component_id, item.method_family_id)),
        element("p", "", t("Outcome: {0} · artifact: {1}", item.outcome_id, item.artifact_id)),
        element("p", "", t("Preregistered source Attempt: {0}", item.source_attempt_id)),
        element("p", "", t("Execution: {0} · source Process only", item.execution_terminal_reason || "unknown")),
        element("p", "", t("Recorded outcome: {0} · local metric: {1}", item.local_outcome, item.local_metric_value ?? "none")),
        element("p", "", t("LOCAL criterion: {0}. Shared GOAL verdict: {1} (run-wide).", !item.local_measurement ? t("not declared") : item.local_outcome === "SUCCESS" ? "passed" : item.local_outcome === "CLEAN_REFUTATION" ? t("not met") : t("unavailable ({0})", item.local_outcome || t("unknown outcome")), status.goal_verdict || "PENDING")));
      if (item.goal_evaluation) {
        card.append(element("h4", "", t("Published observations")));
        const observations = element("dl", "acceptance-observations");
        Object.entries(item.goal_evaluation.measurements || {}).forEach(([name, value]) => observations.append(element("dt", "", name), element("dd", "", value)));
        Object.entries(item.goal_evaluation.checks || {}).forEach(([name, value]) => observations.append(element("dt", "", name), element("dd", "", String(value))));
        card.append(observations, rawDetails(t("Technical source · recorded outcome"), item.goal_evaluation));
      }
      else card.append(element("p", "", item.scope === "LOCAL"
        ? t("LOCAL: not a common-goal evaluation.")
        : t("No accepted common observations published for this outcome.")));
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
          label: rootId || t("{0} (no recorded hypothesis)", outcome.component_id || "unknown"),
          title: root ? (root.claim || rootId)
            : t("These outcomes are not linked to a recorded hypothesis; they are grouped by component."),
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
          comparison: measurement.comparison === "DELTA_FROM_BASELINE" ? t("Delta from baseline") : measurement.comparison === "RELATIVE_DELTA_FROM_BASELINE" ? t("Relative delta from baseline") : measurement.comparison === "VALUE" ? "Value" : t("Local measurement"),
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
    const svg = svgElement("svg", {viewBox: `0 0 ${width} ${height}`, class: "metric-svg", role: "img", "aria-label": t("{0} trend", label)});
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
      label.textContent = t("target {0} {1}", METRIC_OPERATORS[target.operator], formatMetric(target.value));
      svg.append(label);
    }
    visible.forEach(({series, points}) => {
      const d = points.map((point, index) => `${index === 0 ? "M" : "L"} ${x(index).toFixed(1)} ${y(point.value).toFixed(1)}`).join(" ");
      svg.append(svgElement("path", {class: `metric-line ${series.accent}`, d}));
      points.forEach((point, index) => {
        const dot = svgElement("circle", {class: `metric-point ${series.accent}`, cx: x(index).toFixed(1), cy: y(point.value).toFixed(1), r: 3.4});
        const tooltip = svgElement("title");
        tooltip.textContent = `${point.outcome.experiment_id}\n${label} = ${point.raw}\n${point.hypothesisId ? t("hypothesis {0}", point.hypothesisId) : t("no recorded hypothesis")}`;
        dot.append(tooltip);
        svg.append(dot);
      });
    });
    figure.append(element("figcaption", "", target ? t("{0} · target {1} {2}", label, METRIC_OPERATORS[target.operator], formatMetric(target.value)) : label), svg);
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
      value.title = t("Recorded value: {0}", points[0].raw);
      card.append(element("h5", "", series.label), value);
    }
    const context = element("p", "metric-local-context", series.context);
    context.title = series.title;
    const latest = points[points.length - 1].outcome;
    const localStatus = latest.local_outcome === "SUCCESS" ? "passed" : latest.local_outcome === "CLEAN_REFUTATION" ? t("not met") : "unavailable";
    card.append(context, element("p", "panel-copy", t("{0} published observation{1} · {2}", points.length, single ? "" : "s", series.comparison)),
      element("p", "metric-local-criterion", t("Declared LOCAL criterion: {0} · last recorded {1}", series.criterion, localStatus)));
    return card;
  }

  function renderMetricTrends(panel, common, snapshot) {
    const model = metricTrendModel(common, snapshot);
    const section = element("section", "metric-trends");
    section.append(element("h3", "", t("Metric trends")), element("h4", "", t("GOAL evaluation")),
      element("p", "panel-copy", t("Published observations connected per initial-hypothesis chain, in each chain's experiment order. Click a legend entry to hide or show that chain.")));
    if (!model.series.size) section.append(element("p", "metric-empty panel-copy",
      list(common.outcomes).some((outcome) => outcome.goal_evaluation)
        ? t("No numeric GOAL measurements are available. See published experiment records for checks and other results.")
        : t("Waiting for published GOAL measurements. Local screening and running experiments do not produce a final GOAL score.")));
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
          element("p", "", t("Target {0} {1}", METRIC_OPERATORS[target.operator], formatMetric(target.value))),
          element("p", "panel-copy", t("No published measurement")));
        grid.append(card);
      }
    });
    section.append(legend, grid);
    const checkNames = Array.from(new Set([...list(common.acceptance?.validity_checks),
      ...list(common.acceptance?.criteria).filter((c) => c.operator === "IS_TRUE").map((c) => c.name)]));
    if (checkNames.length) {
      const checks = element("details", "metric-checks");
      checks.append(element("summary", "", t("GOAL checks · per experiment")));
      const evaluated = list(common.outcomes).filter((item) => item.goal_evaluation);
      if (!evaluated.length) checks.append(element("p", "panel-copy", checkNames.map((name) => t("{0}: unknown", name)).join(" · ")));
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
      localSection.append(element("h4", "", t("LOCAL screening")), element("p", "panel-copy",
        t("Single observations appear as results; multiple observations form a trend only within one hypothesis and the same measurement definition. Local scores are not GOAL scores and may use different baselines or scales.")));
      const localGrid = element("div", "metric-grid-charts");
      local.metrics.forEach((bucket, metric) => localGrid.append(renderLocalMetric(metric, local)));
      if (!local.metrics.size) localSection.append(element("p", "metric-empty panel-copy", t("Local records exist; numeric values or measurement definitions are unavailable.")));
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
      ? t("◉ OPERATOR API CONTROL{0} · {1}", answerToken ? " / ANSWERS" : "", hasHostStatus ? t("HOST STATUS ENRICHED") : t("OFFLINE PROJECTION"))
      : answerToken
      ? t("◉ OPERATOR ANSWERS · {0}", hasHostStatus ? t("HOST STATUS ENRICHED") : t("OFFLINE PROJECTION"))
      : hasHostStatus ? t("◉ READ-ONLY · HOST STATUS ENRICHED") : t("◉ READ-ONLY · OFFLINE PROJECTION"));
    setText("runtime-source", hasHostStatus ? t("HOST STATUS") : t("OFFLINE"));

    const authority = byId("authority-note");
    const authorityLabel = element("strong", "", t("Authority boundary: "));
    const authorityText = hasHostStatus
      ? t("The resident Host answered this poll, so quiescence, Agent usage, and resource waits below reflect that response. OPEN remains durable state, not process liveness; STATUS is not a continuous liveness signal. COMPLETED means lifecycle settlement, not GOAL_MET. Polls may straddle a commit, and Oracle CLEAN is not a scientific-success verdict.")
      : t("OPEN means durably open in the state store; it does not prove host, worker, or process liveness. Host quiescence, Agent usage, and live resource waits are unavailable offline. COMPLETED means lifecycle settlement, not GOAL_MET. Polls may straddle a commit, and Oracle CLEAN is not a scientific-success verdict.");
    authority.replaceChildren(authorityLabel, document.createTextNode(authorityText));

    if (!hasHostStatus) {
      setText("backup-quiescence", t("Host STATUS unavailable"));
      setText("agent-observed", t("Host STATUS unavailable"));
      setText("agent-budget", t("Host STATUS unavailable"));
    } else {
      const quiescent = status.quiescent_for_backup;
      setText("backup-quiescence", quiescent === true ? "true" : quiescent === false ? "false" : "unknown");
      const usage = object(status.agent_usage);
      const observed = object(usage.observed_lower_bound);
      setText(
        "agent-observed",
        t("tokens {0} · turns {1}", observed.tokens ?? "?", observed.turns ?? "?"),
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
        t("admission total (actual + conservative bounds): ") +
          t("tokens {0}/{1} ({2}; ", tokens.charged_or_reserved ?? "?", tokenLimit, tokenLimitLabel) +
          t("aggregate overage +{0}) · policy {1} · ", tokens.overage ?? 0, modes) +
          t("per-call overages {0}", enforcement.observed_overage_attempts ?? "?") +
          `/+${enforcement.overage_tokens ?? "?"} tokens · ` +
          t("turns {0}/{1} · ", turns.charged_or_reserved ?? "?", turnLimit) +
          t("open reserve {0} attempts / ", openDeclared.attempts ?? "?") +
          t("≤{0} tokens / ≤{1} turns · ", openDeclared.tokens ?? "?", openDeclared.turns ?? "?") +
          t("closed accounting unknown {0} attempts / ", usage.unobserved_closed_attempts ?? "?") +
          t("≤{0} tokens / ≤{1} turns · ", closedUnknown.tokens ?? "?", closedUnknown.turns ?? "?") +
          t("local budget rejection {0} attempts / zero charge", usage.budget_rejected_attempts ?? "?"),
      );
    }

    const waitsContainer = byId("resource-wait-list");
    waitsContainer.replaceChildren();
    for (const process of status.process_activity || []) {
      const grant = process.resource_grant || {};
      const bytes = (process.logs || []).reduce((sum, row) => sum + row.bytes, 0);
      const ages = (process.logs || []).filter(row => row.bytes > 0).map(row => row.age_seconds);
      waitsContainer.append(element("p", "panel-copy", t("{0}: {1} {2} · process {3} · logs {4} bytes{5}. Scientific progress unknown; OPEN records ownership.", process.experiment_id, grant.kind || t("resource unknown"), (grant.gpu_uuids || []).join(", "), process.process_alive === true ? "alive" : process.process_alive === false ? t("exited, collection pending") : t("liveness unknown"), bytes, ages.length ? t(" · last write {0} s ago", Math.round(Math.min(...ages))) : t(" · no output observed"))));
    }
    const waits = status.resource_waits;
    if (!hasHostStatus || !Array.isArray(waits)) {
      waitsContainer.append(element("p", "empty-copy", t("Host STATUS unavailable; live GPU/capacity waits are unknown.")));
    } else if (!waits.length) {
      waitsContainer.append(element("p", "empty-copy", t("No resource wait reported by this Host poll.")));
    } else {
      waits.forEach((value) => {
        const wait = object(value);
        const row = element("div", "resource-wait");
        row.append(
          element("strong", "", wait.experiment_id || t("unnamed workflow")),
          element("span", "", wait.message || wait.reason || t("unspecified resource wait")),
        );
        const planning = object(wait.resource_planning);
        const intent = object(planning.resource_intent);
        if (Number.isFinite(intent.workload_memory_mib)) {
          const budget = object(planning.budget);
          row.append(element("span", "", t("GPU budget {0} MiB + reserve {1} MiB", intent.workload_memory_mib, intent.reserve_memory_mib) +
            (Number.isFinite(budget.sample_peak_mib) && Number.isFinite(budget.headroom_percent)
              ? t(" · measured headroom {0}% · {1}", budget.headroom_percent, intent.sharing)
              : intent.sharing ? t(" · {0} · no complete measurement", intent.sharing) : "")));
        }
        waitsContainer.append(row);
      });
    }
  }

  const EXPERT_STAGES = () => ([
    ["LITERATURE_REVIEW", "LIT", t("Literature review")], ["HYPOTHESIS_PROPOSAL", "HYP", t("Hypothesis proposal")],
    ["EXPERIMENT_DESIGN_IMPLEMENT", "DES", "Design / implementation"], ["EXPERIMENT_EXECUTION", "RUN", t("Experiment execution")],
    ["RESULT_ANALYSIS", "ANA", t("Result analysis")], ["HYPOTHESIS_UPDATE", "UPD", t("Hypothesis update")],
    ["CYCLE_REPORT", "REP", t("Cycle report")], ["ADJUDICATE", "JDG", "Adjudication"], ["SELF_EVOLVE", "EVO", "Self-evolution"],
    ["DEBATE", "DEB", t("Debate round")], ["DEBATE_JUDGE", "JUD", t("Debate judge")], ["LEADER", "LEAD", t("Leader advice")],
  ]);

  function compactNumber(value, lowerBound = false) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "?";
    let amount = value;
    let divisor = 1;
    for (const suffix of ["", "K", "M", "B", "T"]) {
      if (amount < (lowerBound ? 1000 : 999.995) || suffix === "T") {
        // Divide the original count before truncating to avoid floating-point
        // loss from rescaling an already rounded decimal (for example 2.55M).
        const displayed = lowerBound ? Math.floor(value / (divisor / 100)) / 100 : Number(amount.toFixed(2));
        return `${displayed}${suffix}`;
      }
      amount /= 1000;
      divisor *= 1000;
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

  function renderConcurrency(snapshot, trafficFresh) {
    const overview = object(snapshot.overview);
    const observed = object(overview.concurrency);
    const live = connection.dataset.state === "live" && overview.host_observation?.availability === "responded";
    const panel = byId("run-concurrency");
    panel.replaceChildren();
    panel.setAttribute("aria-label", t("Concurrency occupancy"));
    for (const [kind, label] of [["AGENT", "Agents"], ["PROCESS", "Experiments"], ["REVIEW", "Reviews"]]) {
      const slots = object(object(observed.slots)[kind]);
      panel.append(element("span", "", t("{0}: {1} / {2}", t(label), slots.used ?? "—", slots.limit ?? "—")));
    }
    panel.append(element("span", "concurrency-note", live ? t("Observed occupancy") : t("Last observation · activity unconfirmed")));
    panel.title = t("OPEN attempts consume slots; they do not prove process liveness. Agent, experiment and review limits are separate when available.");
    const scheduling = object(snapshot.status?.agent_scheduling);
    const age = Number(snapshot.observed_at) - Number(scheduling.observed_at);
    const dispatchFresh = live && snapshot.status?.run_state === "ACTIVE" && scheduling.observed_at != null && Number.isFinite(age) && Math.abs(age) <= 30;
    const reasons = {
      agent_capacity: "Waiting for an Agent slot", chain_capacity: "Waiting for a research-chain slot",
      chain_busy: "Waiting for work on the same chain", expert_busy: "Waiting for this expert's current work",
      experiment_wait: "Waiting for experiment completion", provider_backoff: "Waiting for API recovery",
      participant_paused: "Research paused", resource_recovery: "Waiting for resource recovery",
      preparing_workspace: "Preparing workspace", preparing_literature: "Preparing literature",
      dispatch_yield: "Waiting for the next dispatch pass", attempts_exhausted: "Automatic recovery ended",
      budget_exhausted: "Agent budget exhausted", run_stopped: "Run is stopping or finished",
    };
    document.querySelectorAll(".expert-preview[data-expert-role]").forEach(card => {
      const summary = object(list(overview.experts).find(item => item.role === card.dataset.expertRole));
      const capacity = card.querySelector(".expert-capacity");
      capacity.textContent = t("Research chains: {0} / {1}", summary.active_chains ?? "—", summary.parallel_activity?.chain_limit ?? "—");
      capacity.title = t("Running experiments still occupy research-chain slots. A full limit alone does not mean another task is waiting.");
      const waits = card.querySelector(".expert-waits");
      waits.replaceChildren();
      const messages = new Set();
      const execution = object(summary.execution_wait);
      if (execution.pending_experiments > 0) messages.add(t("Awaiting experiments: {0} · handed to Host: {1}", execution.pending_experiments, execution.handed_off ?? "—"));
      if (execution.open_controllers > 0) {
        messages.add(t("Execution Agents still holding slots: {0} · since submission {1}", execution.open_controllers,
          execution.oldest_controller_seconds == null ? "—" : formatDuration(0, execution.oldest_controller_seconds)));
        waits.title = t("An open execution session may be checking or repairing work; this is not proof of idle waiting. Only a closed invocation releases its Agent slot.");
      } else waits.removeAttribute("title");
      if (live) {
        for (const wait of list(snapshot.status?.resource_waits)) {
          if (list(execution.experiment_ids).includes(wait.experiment_id)) messages.add(t("Waiting for experiment resources"));
        }
        if (snapshot.status?.dispatch_error) messages.add(t("Dispatch paused · admitted work may still finish"));
      }
      const traffic = object(object(snapshot.status?.provider_traffic?.by_expert)[card.dataset.expertRole]);
      if (trafficFresh && traffic.queued > 0) messages.add(t("API requests queued: {0}", traffic.queued));
      if (dispatchFresh) {
        const tasks = list(scheduling.tasks).filter(item => item.role === card.dataset.expertRole);
        tasks.forEach(item => { if (reasons[item.reason]) messages.add(t(reasons[item.reason])); });
        const opened = list(snapshot.attempts).some(item => item.state === "OPEN" && item.task_scope === "expert" && item.owner_role === card.dataset.expertRole);
        if (!tasks.length && !opened && !execution.pending_experiments && !messages.size) messages.add(t("No Agent task selected on the last dispatch pass"));
      } else if (!messages.size) messages.add(t("Dispatch observation unavailable"));
      if (!live) messages.add(t("Last observation · activity unconfirmed"));
      messages.forEach(message => waits.append(element("p", "", message)));
    });
  }

  function renderExpertActivity(parent, summary, snapshot) {
    const stages = list(summary.activity || summary.stages);
    const host = object(object(snapshot.overview).host_observation).availability ||
      (String(snapshot.status_source || "").endsWith("+resident-host-status") ? "responded" : "unconfirmed");
    const opened = stages.filter((stage) => stage.state === "OPEN");
    const exhausted = stages.some((stage) => stage.operational_status === "recovery_exhausted");
    const status = opened.length ? t("{0} OPEN · Host {1}", opened.length, host) : exhausted ? t("Automatic recovery ended") : stages.length ? t("Last recorded work") : t("No recorded work");
    parent.append(element("p", `expert-work-status ${opened.length && host !== "responded" ? "stage-unconfirmed" : ""}`, t("Cycle {0} · {1}", summary.cycle ?? "—", status)));
    if (snapshot.status?.dispatch_error) parent.append(element("p", "blocked", t("Dispatch paused · admitted work may still finish")));
    const lane = element("div", "expert-stage-lane");
    lane.setAttribute("aria-label", t("Research stages; highlighted stages are current records, not completion percentage"));
    EXPERT_STAGES().forEach(([node, short, name]) => {
      const matches = stages.filter((stage) => stage.node === node);
      const active = matches.filter((stage) => stage.state === "OPEN");
      const recent = active[0] || matches[matches.length - 1];
      const chip = element("span", `stage-chip stage-${recent ? stageState(recent, host) : "pending"}`, `${short}${active.length > 1 ? ` ×${active.length}` : ""}`);
      chip.title = `${name}: ${recent ? recent.terminal_reason || recent.state : t("no current record")}`;
      chip.setAttribute("aria-label", chip.title);
      chip.dataset.stage = node;
      lane.append(chip);
    });
    parent.append(lane);
    const activity = element("div", "expert-current-work");
    stages.forEach((stage) => {
      const phase = stage.failure_stage === "literature_retrieval" ? t("Literature preparation")
        : EXPERT_STAGES().find(([node]) => node === stage.node)?.[2] || stage.node || t("Unknown stage");
      const scope = [stage.kind === "PROCESS" ? "process" : stage.subkind, stage.chain || stage.experiment_id].filter(Boolean).join(" · ");
      const status = stage.terminal_reason || stage.state || "UNKNOWN";
      const row = element("p", `expert-stage-detail stage-${stageState(stage, host)}`);
      row.append(element("strong", "", phase), document.createTextNode(` · ${scope ? `${scope} · ` : ""}${status} · ${stage.elapsed_seconds == null ? "elapsed ?" : formatDuration(0, stage.elapsed_seconds)}`));
      if (stage.operational_status === "recovery_exhausted") row.append(document.createTextNode(t(" · {0} attempts · automatic recovery ended", stage.attempts ?? "?")));
      if (stage.failure_code) row.append(document.createTextNode(` · ${stage.failure_code}`));
      else if (stage.operational_status && stage.operational_status !== "recovery_exhausted") row.append(document.createTextNode(` · ${stage.operational_status}`));
      if (stage.recovery_note) row.title = stage.recovery_note;
      activity.append(row);
    });
    if (!stages.length) activity.append(element("p", "panel-copy", t("Current stage unavailable; open the expert history for retained evidence.")));
    parent.append(activity);
  }

  function renderExpertParallelActivity(parent, summary) {
    const parallel = object(summary.parallel_activity);
    if (Object.keys(parallel).length) {
      const counts = element("div", "expert-parallel-work");
      counts.setAttribute("aria-label", t("Hypotheses, proposal tasks and reading assistants"));
      counts.append(element("p", "panel-copy", t("Active research chains: {0} / {1}", summary.active_chains ?? "?", parallel.chain_limit ?? "?")));
      list(parallel.proposals).forEach((proposal) => {
        const admitted = t("{0} accepted", proposal.accepted_candidates ?? "?");
        counts.append(element("p", "panel-copy", t("{0} · cycle {1} · hypotheses: {2} / limit {3}", proposal.component_id, proposal.cycle, admitted, proposal.candidate_limit ?? "?")));
        if (!list(proposal.stages).length) return;
        const wings = element("p", "panel-copy proposal-wings");
        ["FORWARD", "BACKWARD", "SYNTHESIS"].forEach((name, index) => {
          const stage = list(proposal.stages).find((item) => item.subkind === name);
          const value = !stage ? t("not dispatched") : stage.terminal_reason === "SUCCEEDED" ? "accepted"
            : stage.operational_status === "recovery_exhausted" ? t("failed · recovery ended") : stage.terminal_reason || stage.state;
          if (index) wings.append(document.createTextNode(" · "));
          wings.append(element("span", "", `${name}: ${value}${stage?.attempts > 1 ? t(" ({0} attempts)", stage.attempts) : ""}`));
        });
        counts.append(wings);
      });
      const readers = object(parallel.readers), statuses = object(readers.states), calls = object(readers.calls);
      counts.append(element("p", "panel-copy", t("Reading assistants: {0} tasks · {1} attempts · queued {2} · running {3} · completed {4} · failed {5} · cancelled {6}{7}", readers.tasks ?? "?", readers.attempts ?? "?", statuses.queued ?? "?", statuses.running ?? "?", statuses.completed ?? "?", statuses.failed ?? "?", statuses.cancelled ?? "?", statuses.unknown ? t(" · unknown {0}", statuses.unknown) : "")));
      const prefix = calls.unknown_attempts ? "≥" : "";
      counts.append(element("p", "panel-copy reading-tool-observation", t("Reading tool: {0}{1} calls · {2}{3} rejected{4}{5}", prefix, calls.calls ?? "?", prefix, calls.rejected ?? "?", calls.unknown_attempts ? t(" · {0} attempt traces incomplete/unavailable", calls.unknown_attempts) : "", calls.unanswered ? t(" · {0} results unobserved", calls.unanswered) : "")));
      list(calls.observed_wait_reasons).forEach((reason) => counts.append(element("p", "panel-copy", t("Recorded reading wait: {0} · may have changed", reason))));
      parent.append(counts);
    } else parent.append(element("p", "panel-copy", t("Proposal and reading activity is unavailable in the current observation.")));
  }

  function renderStageTokens(parent, values) {
    const table = element("table", "stage-token-table");
    const heading = element("tr", "");
    ["Stage", "Reported", t("Failed calls"), t("Recovery calls"), t("Open observed"), "Reserved"].forEach((name) => heading.append(element("th", "", name)));
    table.append(heading);
    list(values).forEach((stage) => {
      const row = element("tr", "");
      const usage = object(stage.accounting), recovery = object(stage.context_recovery);
      [stage.subkind ? `${stage.node} / ${stage.subkind}` : stage.node,
        usage.reported_tokens, usage.failed_reported_tokens, recovery.recovery_reported_tokens,
        usage.observed_unsettled_tokens, usage.reserved_tokens].forEach((value, index) =>
          row.append(element("td", "", index ? (Number.isInteger(value) ? compactNumber(value) : "unknown") : value)));
      row.title = t("Usage lower bounds; unknown closed calls: {0}; unavailable token records: {1}", usage.unknown_closed_attempts ?? '?', usage.unavailable_token_attempts ?? '?');
      table.append(row);
    });
    if (list(values).length) parent.append(table);
    else parent.append(element("p", "panel-copy", t("Stage token accounting unavailable.")));
    parent.append(element("p", "panel-copy", t("Failed and recovery calls are subsets of reported usage and may overlap. Open observations and reserves are separate; reserves are not consumption. Missing usage is unknown.")));
  }

  function renderExpertDetails(parent, role) {
    const snapshot = state.snapshot || {};
    const summary = object(list(object(snapshot.overview || state.product?.summary).experts)
      .find((item) => item.role === role));
    parent.append(element("h3", "", t("Current work")));
    renderExpertActivity(parent, summary, snapshot);
    list(summary.activity).filter((stage) => stage.operational_status === "recovery_exhausted").forEach((stage) => {
      parent.append(element("p", "panel-copy", stage.recovery_note || t("Automatic recovery ended for this node.")));
      parent.append(actionButton(t("Failure details"), () => openRemoteDetail("attempt", {attempt_id: stage.attempt_id}), "detail-link"));
    });
    parent.append(element("h3", "", t("Tokens by stage")));
    renderStageTokens(parent, summary.stage_accounting);
    parent.append(element("h3", "", t("Research chains, proposals and reading")));
    renderExpertParallelActivity(parent, summary);
    parent.append(actionButton(t("Browse expert history"), () => {
      byId("filter-expert").value = role;
      closeDrawer();
      renderFilteredViews();
      openWorkbench("sessions");
    }));
  }

  function taskScopeLabel(record) {
    return record.task_scope === "run" ? (record.run_group === "grounding" ? "Grounding" : "Global") : record.task_scope === "expert"
      ? t("Expert · {0}", record.owner_role || "unavailable") : t("Ownership unknown");
  }

  function renderGlobalTasks(snapshot) {
    renderGrounding(snapshot);
    const container = byId("global-task-list");
    container.replaceChildren();
    const overview = object(snapshot.overview);
    [["run", "global_tasks", "Global"], ["unknown", "unknown_tasks", t("Ownership unknown")]].forEach(([scope, key, title]) => {
      const group = object(key === 'global_tasks' ? (overview.other_global_tasks || overview[key]) : overview[key]);
      if (!list(group.stages).length) return;
      const row = element("article", "global-task-summary");
      const opened = list(group.activity).filter((item) => item.state === "OPEN");
      const stages = opened.length ? opened : list(group.stages).slice(-1);
      stages.forEach((stage) => {
        const button = actionButton(`${stage.node || "Task"} · ${stage.state === "OPEN" ? "OPEN" : stage.terminal_reason || stage.state} · ${compactDuration(stage.elapsed_seconds)}`,
          () => openRemoteDetail("attempt", {attempt_id: stage.attempt_id}), "detail-link global-task-attempt");
        button.title = t("{0} task · OPEN is durable state, not process liveness", title);
        row.append(button);
      });
      const usage = object(group.accounting);
      row.append(element("span", "panel-copy", t(" {0} · ≥{1} tokens · reserved {2} ", title, compactNumber(usage.reported_tokens), compactNumber(usage.reserved_tokens))));
      row.append(actionButton("Details & history", () => openLocalDetail("global_tasks", {task_scope: scope, name: t("{0} tasks", title)}), "detail-link"));
      container.append(row);
    });
    if (!container.children.length) container.append(element("p", "empty-copy", t("No global task evidence yet.")));
    renderTraffic(snapshot);
  }

  function renderGrounding(snapshot) {
    const container = byId('grounding-agent-card');
    const restorePulses = preservePulseTiming(container);
    const wasOpen = container.querySelector('details')?.open;
    const previousLight = container.querySelector('.breath-dot');
    container.replaceChildren();
    const group = object(snapshot.overview?.grounding);
    const sharing = object(snapshot.overview?.grounding_sharing);
    const card = element('article', 'entity-card grounding-card');
    const heading = element('h3', '', 'Grounding');
    if (group.state === 'active') {
      const confirmed = object(snapshot.overview).host_observation?.availability === 'responded';
      const light = previousLight || element('span', 'breath-dot');
      light.classList.toggle('host-confirmed', confirmed);
      light.classList.toggle('host-unconfirmed', !confirmed);
      light.id = 'grounding-pulse';
      heading.prepend(light);
    }
    const badge = element('span', 'grounding-badge', 'UNKNOWN');
    badge.id = 'grounding-state'; heading.append(badge);
    card.append(heading, element('p', 'panel-copy', t('Run-wide preparation · separate from expert work')));
    const rpm = element('p', 'grounding-rpm', t('Grounding RPM') + ' ');
    const rpmValue = element('strong', '', '—'); rpmValue.id = 'grounding-rpm'; rpm.append(rpmValue);
    card.append(rpm, element('p', 'panel-copy', t('≥{0} tokens · task span {1} · active readers {2}',
      compactNumber(object(group.accounting).reported_tokens), typeof group.elapsed_seconds === 'number' ? compactDuration(group.elapsed_seconds) : '—', group.active_readers ?? '—')));
    const preview = element('div', 'reliability-preview');
    preview.setAttribute('aria-label', t('{0} cumulative execution reliability', 'Grounding'));
    compactReliability(preview, object(group.reliability).metrics, {taskScope: 'run', runGroup: 'grounding'});
    card.append(preview);
    const details = document.createElement('details');
    details.open = wasOpen ?? group.state !== 'completed';
    details.append(element('summary', '', t('Tasks & details')));
    const ids = new Set(list(group.attempt_ids));
    const working = list(snapshot.attempts).filter(a => ids.has(a.attempt_id) && a.kind === 'AGENT' && a.state === 'OPEN');
    const tiles = element('div', 'agent-tile-list');
    for (const attempt of working) {
      const entry = workingTile(attempt, null);
      updateWorkingTile(entry, attempt, attempt.task_kind || attempt.command || 'Grounding',
        object(snapshot.overview).host_observation?.availability === 'responded', Number(snapshot.observed_at));
      tiles.append(entry.tile);
    }
    for (const [id, entry] of state.workingTiles) {
      if (entry.role === null && !working.some(a => a.attempt_id === id)) state.workingTiles.delete(id);
    }
    details.append(tiles);
    for (const stage of list(group.activity)) {
      details.append(actionButton(`${stage.node} · ${stage.state} · ${compactDuration(stage.elapsed_seconds)}`,
        () => openRemoteDetail('attempt', {attempt_id:stage.attempt_id}), 'detail-link'));
    }
    if (sharing.state === "accepted") card.append(element("p", "panel-copy grounding-sharing",
        t("Grounding accepted · supplied to {0}/{1} experts' task inputs", list(sharing.experts).filter((item) => item.provided).length, list(sharing.experts).length)));
    else if (sharing.state === "waiting_human") card.append(element("p", "panel-copy", t("Grounding awaiting clarification · review the questions above.")));
    if (sharing.source_attempt_id) details.append(actionButton(t('Grounding source'),
      () => openRemoteDetail('attempt', {attempt_id:sharing.source_attempt_id}), 'detail-link'));
    details.append(actionButton('Details & history', () => openLocalDetail('global_tasks',
      {task_scope:'run', name:t('Global tasks')}), 'detail-link grounding-history'));
    card.append(details); container.append(card);
    restorePulses();
  }

  function renderGlobalDetails(parent, scope) {
    const overview = object(state.snapshot?.overview);
    const group = object(overview[scope === "run" ? "global_tasks" : "unknown_tasks"]);
    if (scope === "run") {
      const sharing = object(overview.grounding_sharing);
      parent.append(element("h3", "", t("Grounding sharing")), element("p", "panel-copy",
        sharing.state === "accepted" ? t("Accepted result available to subsequent tasks. Input delivery does not establish reading or understanding.")
          : sharing.state === "waiting_human" ? t("Awaiting clarification; no final accepted result.")
          : t("No final accepted Grounding result available.")));
      if (sharing.source_attempt_id) parent.append(actionButton(t("Grounding source"), () => openRemoteDetail("attempt", {attempt_id: sharing.source_attempt_id}), "detail-link"));
      list(sharing.experts).forEach((expert) => parent.append(element("p", "panel-copy",
        `${expert.role} · ${expert.provided ? t("provided in task inputs") : t("delivery not yet observed")}`)));
    }
    parent.append(element("h3", "", "Usage & recovery"));
    structuredDetail(parent, {tokens: group.accounting, context_recovery: Object.fromEntries(
      Object.entries(object(group.context_recovery)).filter(([key]) => !/cost|pricing|usd/i.test(key)))});
    compactReliability(parent, object(group.reliability).metrics, {expanded: true, taskScope: scope});
    parent.append(element("h3", "", t("Recorded tasks")));
    list(group.stages).slice(-20).forEach((stage) => parent.append(actionButton(t("{0} · {1} · execution role {2}", stage.node, stage.terminal_reason || stage.state, stage.role || "unknown"),
      () => openRemoteDetail("attempt", {attempt_id: stage.attempt_id}), "detail-link")));
    parent.append(actionButton(t("Browse task history"), () => {
      closeDrawer(); openWorkbench("sessions", {task_scope: scope});
    }));
  }

  /*
   * Lucide bot and clock icon shapes: https://lucide.dev/icons/{bot,clock}
   * ISC License
   * Copyright (c) 2026 Lucide Icons and Contributors
   *
   * Permission to use, copy, modify, and/or distribute this software for any
   * purpose with or without fee is hereby granted, provided that the above
   * copyright notice and this permission notice appear in all copies.
   *
   * THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
   * WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
   * MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
   * ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
   * WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
   * ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
   * OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
   *
   * The clock icon is derived from Feather, under the MIT License:
   * Copyright (c) 2013-present Cole Bemis
   *
   * Permission is hereby granted, free of charge, to any person obtaining a copy
   * of this software and associated documentation files (the "Software"), to deal
   * in the Software without restriction, including without limitation the rights
   * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   * copies of the Software, and to permit persons to whom the Software is
   * furnished to do so, subject to the following conditions:
   *
   * The above copyright notice and this permission notice shall be included in all
   * copies or substantial portions of the Software.
   *
   * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
   * SOFTWARE.
   */
  function workingTileIcon(kind) {
    const icon = svgElement("svg", {
      viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
      "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", focusable: "false",
    });
    if (kind === "AGENT") {
      icon.append(
        svgElement("path", {d: "M12 8V4H8"}),
        svgElement("rect", {width: 16, height: 12, x: 4, y: 8, rx: 2}),
        svgElement("path", {d: "M2 14h2 M20 14h2 M15 13v2 M9 13v2"}),
      );
    } else {
      icon.append(svgElement("circle", {cx: 12, cy: 12, r: 10}), svgElement("path", {d: "M12 6v6l4 2"}));
    }
    return icon;
  }

  function workingTile(attempt, role) {
    let entry = state.workingTiles.get(attempt.attempt_id);
    if (entry) return entry;
    const light = element("span", "agent-tile-light");
    const fill = element("span", "agent-tile-fill");
    const value = element("span", "agent-tile-value");
    const bar = element("span", "agent-tile-bar");
    bar.append(fill, value);
    const tile = element("button", "agent-tile");
    tile.type = "button";
    tile.dataset.attempt = attempt.attempt_id;
    tile.dataset.kind = attempt.kind;
    const metric = element('span', 'agent-tile-metric');
    metric.setAttribute('aria-hidden', 'true');
    metric.append(workingTileIcon(attempt.kind));
    tile.append(light, bar, metric);
    tile.addEventListener("click", () => {
      if (role === null) { openRemoteDetail('attempt', {attempt_id:attempt.attempt_id}); return; }
      byId("filter-expert").value = role || '';
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
    tile.querySelector('.agent-tile-light').classList.toggle('host-confirmed', hostResponded);
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
      : priorKnown ? t("last {0}", compactNumber(context.last_observed_input_tokens)) : "—";
    value.textContent = attempt.operational_status === "compacting" ? `↻ ${valueText}` : valueText;
    fill.style.width = known && limit ? `${Math.min(100, Math.round((attempt.context_tokens / limit) * 1000) / 10)}%` : "0%";
    const lines = [t("{0} · attempt {1}", label, attempt.attempt_id)];
    if (isProcess) {
      lines.push(t("elapsed {0} · OPEN is durable state, not process liveness", compactDuration(elapsed)));
    } else {
      if (separatedContext) {
        const currentExchange = context.current_request_exchange ?? "unknown";
        lines.push(t("Current request: exchange {0} · {1}", currentExchange, known ? t("{0} input tokens · {1}", compactNumber(attempt.context_tokens), attempt.context_source || t("unknown source")) : t("provider input usage pending or unavailable")));
        lines.push(priorKnown ? t("Last measured input: {0} tokens · exchange {1}{2}", compactNumber(context.last_observed_input_tokens), context.last_observed_exchange ?? "unknown", context.last_observed_exchange !== context.current_request_exchange ? t(" (earlier request)") : "") : t("Last measured input: unavailable"));
        const estimate = context.current_request_estimated_input_tokens;
        const estimateSources = {provider_count_tokens: t("provider token count"), provider_usage_plus_delta_estimate: t("prior measured usage plus estimated added input"), utf8_bytes_conservative_estimate: t("conservative UTF-8 byte estimate")};
        lines.push(t("Current request estimate: {0} · {1}", Number.isInteger(estimate) ? compactNumber(estimate) : "unknown", estimateSources[context.current_request_estimate_source] || context.current_request_estimate_source || t("unknown source")));
        if (typeof context.current_request_fits === "boolean") lines.push(t("Current request capacity check: {0} input allowance", context.current_request_fits ? "within" : "exceeds"));
        if (context.current_request_estimate_reason) lines.push(t("Estimate basis: {0}", context.current_request_estimate_reason));
      } else {
        lines.push(!known ? t("no context reading yet")
          : t("last input {0}{1} · {2}", compactNumber(attempt.context_tokens), limit ? t(" / {0} working context limit", compactNumber(limit)) : t(" · context window unknown"), attempt.context_source || t("unknown source")));
        if (Number.isInteger(context.estimated_input_tokens)) lines.push(t("Estimated input: {0} (conservative UTF-8 byte estimate, separate from provider usage)", compactNumber(context.estimated_input_tokens)));
      }
      if (attempt.context_window_source === "working_limit_only") lines.push(t("Provider window unconfirmed; limit is the framework working estimate."));
      if (Number.isInteger(attempt.cumulative_tokens)) lines.push(t("Cumulative invocation usage: {0} tokens", compactNumber(attempt.cumulative_tokens)));
      if (Number.isInteger(context.available_input_tokens)) lines.push(t("Admission input allowance: {0} · output reserve {1} · tools {2} · margin {3}", compactNumber(context.available_input_tokens), context.output_reserve_tokens ?? "?", context.tool_reserve_tokens ?? "?", context.estimate_margin_tokens ?? "?"));
      if (Number.isInteger(context.declared_context_tokens)) lines.push(t("Configured provider window: {0} tokens", compactNumber(context.declared_context_tokens)));
      if (Number.isInteger(context.cache_read_input_tokens)) lines.push(t("Cache read: {0} input tokens", compactNumber(context.cache_read_input_tokens)));
      if (Number.isInteger(context.cache_creation_input_tokens)) lines.push(t("Cache creation: {0} input tokens", compactNumber(context.cache_creation_input_tokens)));
      if (attempt.session_scope) lines.push(t("Session scope: {0}", attempt.session_scope));
      const observation = attempt.context_observation || {};
      if (Number.isInteger(observation.native_compaction_count)) lines.push(t("Native compactions completed: {0} · explicit starts {1} · completion unobserved {2} · observed duration {3} ms · unknown durations {4} · transcript coverage {5} · start-event coverage {6}", observation.native_compaction_count, observation.native_compaction_started_count ?? "unknown", observation.native_compaction_completion_unobserved ?? "unknown", observation.native_compaction_duration_ms, observation.native_compaction_duration_unknown, observation.coverage, observation.native_compaction_start_coverage ?? "unavailable"));
      const compact = observation.last_native_compaction;
      if (compact) lines.push(t("Last native compaction: {0} → {1} input tokens · {2} ms", compact.pre_tokens ?? "?", compact.post_tokens ?? "?", compact.duration_ms ?? "?"));
      if (Number.isInteger(observation.read_calls)) lines.push(t("Successful Read calls: {0} · exact frozen-input rereads {1} (shell reads excluded)", observation.read_calls, observation.exact_reread_calls));
      if (attempt.operational_status === "compacting") lines.push(t("Native CLI last reported: compacting"));
      lines.push(t("elapsed {0}{1} · OPEN is durable state, not process liveness", compactDuration(elapsed), hasWall ? t(" of {0} wall budget{1}", compactDuration(attempt.wall_seconds), overdue ? " · exceeded" : "") : ""));
    }
    tile.title = lines.join("\n");
    tile.dataset.workTitle = tile.title;
    tile.dataset.workLabel = `${label}: ${valueText}`;
    tile.setAttribute("aria-label", tile.dataset.workLabel);
    if (isProcess) tile.querySelector('.agent-tile-metric').title = lines[1];
  }

  function pauseNotice(pause, inDebate) {
    const reason = typeof pause.reason === "string" ? pause.reason : "";
    const scope = [pause.scope?.component_id, pause.scope?.chain].filter(Boolean).join(" / ");
    const debatePause = reason.startsWith("debate:");
    const active = debatePause && inDebate;
    const notice = element("div", `pause-notice${active ? " in-debate" : debatePause ? "" : " neutral"}`);
    const title = element("p", "pause-title");
    title.append(element("span", "pause-glyph", active ? "⚔" : "⏸"),
      document.createTextNode(active ? t("Debate in progress · this expert is a debater")
        : debatePause ? t("Debate proposed · dispatch paused") : t("Dispatch paused")));
    notice.append(title, element("p", "pause-scope", t("{0}admitted work may finish", scope ? `${scope} · ` : "")));
    if (active) notice.append(actionButton("Leader & Debate →", () => openWorkbench("interactions"), "detail-link pause-link"));
    notice.title = [
      t("Pause reason: {0}", reason || "unspecified"),
      scope ? t("Scope: {0}", scope) : t("Scope: all admitted work of this expert"),
      t("Pause blocks new design / experiment / literature dispatch only; OPEN remains durable state, not process liveness."),
    ].join("\n");
    return notice;
  }

  function renderExperts(snapshot) {
    const experts = list(snapshot.experts).filter((expert) => expertMatches(expert));
    const container = byId("expert-list");
    const restorePulses = preservePulseTiming(container);
    const previousLights = new Map(Array.from(container.querySelectorAll('.expert-preview[data-expert-role]'),
      card => [card.dataset.expertRole, card.querySelector('.breath-dot')]));
    const previousTiles = new Set();
    container.querySelectorAll(".agent-tile[data-attempt]").forEach((tile) => previousTiles.add(tile.dataset.attempt));
    container.replaceChildren();
    const seenTiles = new Set();
    const tileLists = new Map();
    byId("experts-panel").classList.toggle("shared-goal-experts", sharedGoal(snapshot));
    setText("expert-count", experts.length);
    if (!experts.length) {
      container.append(element("p", "empty-copy", t("No expert projection.")));
      return;
    }
    const summaries = list(object(snapshot.overview || object(state.product).summary).experts);
    experts.forEach((expert) => {
      const summary = object(summaries.find((item) => item.role === expert.role));
      const card = element("article", `entity-card expert-preview expert-accent ${expertClass(snapshot, expert.role)}`);
      card.dataset.expertRole = expert.role;
      const identity = element("div", "expert-identity");
      const openAttempts = list(snapshot.attempts).filter((attempt) => attempt && attempt.task_scope === "expert" && attempt.owner_role === expert.role && attempt.state === "OPEN").length;
      const heading = element("h3", "", expert.role || t("unnamed role"));
      if (openAttempts) {
        const confirmed = object(snapshot.overview).host_observation?.availability === "responded";
        const breath = previousLights.get(expert.role) || element("span", "breath-dot");
        breath.classList.toggle("host-confirmed", confirmed);
        breath.classList.toggle("host-unconfirmed", !confirmed);
        breath.title = t("{0} durably open attempt{1} · {2} · OPEN is durable state, not process liveness", openAttempts, openAttempts === 1 ? "" : "s", confirmed ? t("Host responded this poll") : t("Host contact unconfirmed"));
        breath.dataset.workTitle = breath.title;
        breath.setAttribute("aria-label", confirmed ? t("Open work; Host responded") : t("Open work; activity unknown"));
        heading.prepend(breath);
      }
      identity.append(element("p", "eyebrow", t("EXPERT {0}", String(expert.slot_ordinal ?? "?").padStart(2, "0"))), heading);
      const rpm = element("p", "expert-rpm", "RPM ");
      const rpmValue = element("strong", "expert-rpm-value", "—");
      rpmValue.dataset.rpmValue = "";
      rpm.append(rpmValue);
      identity.append(rpm);
      identity.append(element("p", "expert-capacity"), element("div", "expert-waits"));
      const working = list(snapshot.attempts).filter((attempt) => attempt && attempt.task_scope === "expert" && attempt.owner_role === expert.role && attempt.state === "OPEN"
        && (attempt.kind === "AGENT" || attempt.kind === "PROCESS"));
      if (object(summary.leader).active > 0) {
        const crown = element("span", "leader-crown", "♛");
        crown.title = t("Active Leader coordination · open details for advice or retained round semantics");
        crown.setAttribute("aria-label", t("Active Leader"));
        heading.append(crown);
      }
      if (working.length) {
        const stageByCommand = new Map(EXPERT_STAGES().map(([command, short, label]) => [command, label]));
        const observedAt = Number(snapshot.observed_at) || Date.now() / 1000;
        const hostResponded = object(snapshot.overview).host_observation?.availability === "responded";
        const sorted = [...working].sort((a, b) => (Number(a.started_at) - Number(b.started_at))
          || String(a.attempt_id).localeCompare(String(b.attempt_id)));
        const wrap = element("div", "working-agents");
        wrap.append(element("p", "subagent-title", t("Working · {0}", working.length)));
        const tiles = element("div", "agent-tile-list");
        sorted.slice(0, 6).forEach((attempt) => {
          const entry = workingTile(attempt, expert.role);
          seenTiles.add(attempt.attempt_id);
          const label = attempt.kind === "PROCESS" ? t("Experiment process")
            : stageByCommand.get(attempt.command) || attempt.command || "unknown";
          updateWorkingTile(entry, attempt, label, hostResponded, observedAt);
          tiles.append(entry.tile);
        });
        if (working.length > 6) tiles.append(element("span", "agent-tile more", t("+{0} more", working.length - 6)));
        wrap.append(tiles);
        identity.append(wrap);
        tileLists.set(expert.role, tiles);
      }
      if (sharedGoal(snapshot)) {
        list(snapshot.components).filter((component) => component.owner_role === expert.role).forEach((component) => {
          identity.append(element("p", "expert-progress", t("Progress: {0} · {1}", component.phase || t("not started"), component.state || "unavailable")));
          if (component.blocked) identity.append(element("p", "blocked", t("Blocked: {0}", list(component.blocked_reasons).join("; ") || "unspecified")));
          if (component.revision_warning) identity.append(element("p", "blocked", t("Repeated revisions without convergence; cause unverified · {0} revisions in cycle {1}", component.cycle_revisions ?? 0, component.current_cycle || "—")));
        });
      } else {
        identity.append(element("p", "", t("owns: {0}", list(expert.owned_component_ids).join(", ") || "—")));
        identity.append(element("p", "", t("proposes: {0}", list(expert.proposing_component_ids).join(", ") || "—")));
      }
      list(expert.pause_requested).forEach((pause) => {
        identity.append(pauseNotice(pause, working.some((attempt) => attempt.command === "DEBATE" || attempt.command === "DEBATE_JUDGE")));
      });
      const inspect = actionButton(t("Expert details"), () =>
        openLocalDetail("expert", {role: expert.role, name: t("Expert · {0}", expert.role)}));
      inspect.dataset.expertDetails = expert.role;
      identity.append(inspect);
      const skills = actionButton(t("Skill library"), () => {
        byId("filter-expert").value = expert.role;
        openWorkbench("skills");
      });
      skills.dataset.expertSkills = expert.role;
      identity.append(skills);
      const work = element("div", "expert-work");
      renderExpertActivity(work, summary, snapshot);
      const preview = element("div", "reliability-preview");
      preview.setAttribute("aria-label", t("{0} cumulative execution reliability", expert.role));
      compactReliability(preview, object(summary.reliability).metrics, {role: expert.role});
      work.append(preview);
      const accounting = object(summary.accounting);
      if (accounting.observed_unsettled_tokens > 0) {
        work.append(element("p", "expert-token-usage", t("+≥{0} observed tokens · unsettled calls; included separately from reported usage and reserves", compactNumber(accounting.observed_unsettled_tokens))));
      }
      work.append(element("p", "expert-token-usage", t("🔢 ≥{0} tok · reserve {1} · unknown closed {2} · unavailable token records {3}", compactNumber(accounting.reported_tokens), compactNumber(accounting.reserved_tokens), accounting.unknown_closed_attempts ?? "?", accounting.unavailable_token_attempts ?? "?")));
      const recovery = object(summary.context_recovery);
      if (recovery.context_failure_attempts > 0) {
        const retries = recovery.recovery_attempts || 0;
        const detail = element("p", "expert-token-usage", t("Context recovery: {0} attempts · reported ≥{1} tokens · usage unknown {2}", retries, compactNumber(recovery.recovery_reported_tokens), recovery.recovery_unknown_token_attempts ?? "?"));
        detail.title = t("Subsequent attempts after the first context failure, including successful recovery. Initial failures are separate in command accounting. Unknown usage is not zero; observed usage is not measured savings.");
        work.append(detail);
      }
      work.append(element("p", "panel-copy", t("{0} experiments · {1} papers · {2} skills · {3} effective evolutions", displayValue(object(summary.experiments).total), displayValue(object(summary.literature).retrieved), displayValue(object(summary.skills).count), displayValue(object(summary.evolutions).changed))));
      card.append(identity, work);
      container.append(card);
    });
    for (const [attemptId, entry] of state.workingTiles) {
      if (entry.role === null) continue;
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
    renderTraffic(snapshot);
    restorePulses();
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
      container.append(element("p", "empty-copy", t("No component projection.")));
      return;
    }
    components.forEach((component) => {
      const card = element(
        "button",
        `entity-card expert-accent ${expertClass(snapshot, component.owner_role)}`,
      );
      card.type = "button";
      card.setAttribute("aria-label", t("Inspect component {0}", component.component_id));
      const stateLine = element("p", "state-line");
      stateLine.append(
        element("span", "", compact(component.tier, 24)),
        element("span", component.blocked ? "blocked" : "", compact(component.state, 24)),
      );
      card.append(stateLine);
      card.append(element("h3", "", component.component_id));
      card.append(element("p", "", t("owner {0} · phase {1}", component.owner_role || "—", component.phase || "—")));
      if (component.blocked) {
        card.append(element("p", "blocked", t("blocked: {0}", list(component.blocked_reasons).join("; ") || "unspecified")));
      } else {
        card.append(element("p", "", t("{0} durably open attempts", list(component.open_attempt_ids).length)));
      }
      if (component.revision_warning) {
        card.append(element("p", "blocked", t("repeated revisions without convergence; cause unverified · circuit breaker {0}: {1} revisions in cycle {2}", component.revision_warning, component.cycle_revisions ?? 0, component.current_cycle || "—")));
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
      container.append(element("p", "empty-copy", t("No matching durable OPEN activity.")));
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
      container.append(element("p", "empty-copy", t("No matching durable rendezvous records.")));
      return;
    }
    records.forEach((record) => {
      const card = element("article", "activity-card rendezvous-card");
      const heading = element("div", "activity-heading-row");
      heading.append(
        element("h3", "", record.rendezvous_id || t("unnamed rendezvous")),
        element("span", `status-label ${statusClass(record.status)}`, record.status || "—"),
      );
      const target = record.responder_role || record.target_role || t("unassigned target");
      card.append(heading);
      card.append(element("p", "relation-line", `${record.requester_role || "—"} → ${target}`));
      card.append(element("p", "", `${record.kind || "—"} · ${record.component_id || "—"} · ${record.phase || "—"}`));
      card.append(element("p", "", record.question || t("No question projected.")));
      if (record.disposition || record.summary) {
        card.append(element("p", "resolution-line", `${record.disposition || "resolved"} · ${record.summary || t("no summary")}`));
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
    appendDefinition(grid, "lineage", hypothesis.parent_hypothesis_id ? t("Derived hypothesis") : t("Root hypothesis"));
    if (hypothesis.parent_hypothesis_id) {
      appendDefinition(grid, t("parent claim"), parent?.claim || t("Parent record unavailable"));
      appendDefinition(grid, t("parent method family"), parent?.method_family_id || "Unavailable");
      appendDefinition(grid, t("parent scientific assessment"), parent ? parent.assessment || t("Not assessed") : "Unavailable");
    }
  }

  function graphStatus(item) {
    if (item.kind !== "hypothesis") return {label: item.state, className: statusClass(item.state)};
    const failure = hypothesisFailure(item);
    if (failure) return {label: failure === "Refuted" ? "Refuted" : t("Execution failed"), className: "state-blocked", failure};
    const assessment = item.raw.assessment || item.raw.state;
    if (assessment === "SUPPORTED") return {label: "Supported", className: "state-accepted"};
    if (assessment === "INCONCLUSIVE") return {label: "Inconclusive", className: "state-other"};
    return {label: t("Not assessed"), className: "state-pending"};
  }

  function isPruned(item) {
    return item.kind === "hypothesis" && (item.raw.state === "PRUNED" || list(item.raw.pruned_by).length > 0);
  }

  function topologyEdges(includePruning = state.showPruning) {
    const visible = new Set(state.topology.map((item) => item.id));
    return state.topology.flatMap((item) => [
      ...item.dependencies.map((source) => ({source, target: item.id,
        kind: item.kind === "hypothesis" ? "lineage" : "dependency",
        relation: item.kind === "hypothesis" ? item.raw.relation || t("parent to child") : "dependency"})),
      ...(includePruning ? list(item.pruningRelations).map((edge) => ({...edge, kind: "pruning"})) : []),
    ]).filter((edge) => visible.has(edge.source) && visible.has(edge.target));
  }

  function relationLabel(edge) {
    return edge.kind === "pruning" ? t("Winner → pruned hypothesis")
      : edge.kind === "lineage" ? t("Parent → derived hypothesis") : "Dependency → dependent";
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
          subtitle: `${hypothesis.proposer_role || t("unknown proposer")} · ${hypothesis.chain || hypothesis.method_family_id || "method ?"}`,
          detail: [hypothesis.cycle ? t("cycle {0}", hypothesis.cycle) : "", hypothesis.selected_tip ? "selected" : "", componentId || "component ?"].filter(Boolean).join(" · "),
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
          subtitle: component.name || t("{0} contract", component.tier || "component"),
        detail: t("{0} · owner {1}", view.phase || t("not started"), view.owner_role || component.owner || "—"),
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
    announce(t("Selected {0} {1}", item.kind, item.id));
  }

  function renderFallback(positioned, edges) {
    topologyList.replaceChildren();
    positioned.forEach((item) => {
      const parent = item.kind === "hypothesis"
        ? t("parent: {0}", item.raw.parent_hypothesis_id || t("none (root hypothesis)"))
        : t("depends on {0}", item.dependencies.join(", ") || "nothing");
      const listItem = element("li");
      const button = element("button", "", `${item.id} — ${item.title}; ${parent}; ${graphStatus(item).label}${isPruned(item) ? t("; pruned (control decision)") : ""}`);
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
    relationTooltip.textContent = t("{0} · {1} directly connected node{2}", item.id, count, count === 1 ? "" : "s");
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
      item.append(sample, document.createTextNode(t("Pruned (control, not refutation)")));
      controls.push(item);
    }
    group("Control", controls);
    group(t("Experts"), Array.from(new Set(state.topology.map((item) => item.role))).map((role) =>
      element("span", `legend-item expert-key ${expertClass(snapshot, role)}`, role || t("Unknown expert"))));
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
        "aria-label": t("{0} to {1}, {2}", source.id, target.id, relationLabel(record)),
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
      const lineage = item.kind === "hypothesis" ? (item.raw.parent_hypothesis_id ? t("Derived hypothesis") : t("Root hypothesis")) : "";
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
    pruningControl.textContent = t("{0} pruning links ({1})", state.showPruning ? t("Hide") : t("Show"), pruningCount);
    byId("show-components").classList.toggle("active", !hypothesisMode);
    byId("show-components").setAttribute("aria-pressed", String(!hypothesisMode));
    byId("show-hypotheses").classList.toggle("active", hypothesisMode);
    byId("show-hypotheses").setAttribute("aria-pressed", String(hypothesisMode));
    byId("graph-heading").textContent = hypothesisMode ? t("Hypothesis lineage") : t("Component dependencies");
    const filterNote = state.topology.length < allItems.length
      ? t(" Filtered endpoints and their edges are omitted.")
      : "";
    byId("graph-summary").textContent = hypothesisMode
      ? t("{0} of {1} durable hypotheses shown. Solid arrows: parent → derived hypothesis. Dashed arrows: winner → pruned hypothesis ({2}). Pruning does not change ancestry or scientific assessment. Select a node or link for recorded revision details.{3}", state.topology.length, allItems.length, state.showPruning ? "shown" : "hidden", filterNote)
      : t("{0} of {1} contract components shown. Edges point from dependency to dependent.{2}", state.topology.length, allItems.length, filterNote);
    byId("graph-title").textContent = hypothesisMode ? t("Hypothesis lineage graph") : t("Component dependency graph");
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
    const heading = element("h3", "", t("Selection"));
    heading.id = "selection-heading";
    container.replaceChildren(heading);
    if (!selection) {
      container.append(element("p", "", t("Select a graph node or component to narrow the durable record.")));
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
      appendDefinition(grid, t("scientific assessment"), hypothesis.assessment || t("Not assessed"));
      appendDefinition(grid, "claim", hypothesis.claim || "—");
      appendDefinition(grid, "proposer", hypothesis.proposer_role || "—");
      appendDefinition(grid, t("method family"), hypothesis.method_family_id || "—");
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
    const sessions = element("button", "detail-link", t("Browse related sessions"));
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
      container.append(element("p", "empty-copy", t("No durable attempts in this scope.")));
      return;
    }
    attempts.forEach((attempt) => {
      const card = element("article", "attempt-card");
      card.append(element("h4", "", attempt.attempt_id || t("unnamed attempt")));
      const grid = element("dl");
      appendDefinition(grid, "state", attempt.state === "OPEN" ? t("OPEN (durable; liveness unknown)") : attempt.state || "—");
      appendDefinition(grid, "kind / phase", `${attempt.kind || "—"} / ${attempt.phase || "—"}`);
      appendDefinition(grid, t("task ownership"), taskScopeLabel(attempt));
      appendDefinition(grid, t("execution role"), attempt.role || "—");
      appendDefinition(grid, "ordinal", attempt.attempt_ordinal ?? "—");
      appendDefinition(grid, "workflow", attempt.workflow_id || "—");
      appendDefinition(grid, "started", formatTimestamp(attempt.started_at));
      appendDefinition(grid, "duration", formatDuration(attempt.started_at, attempt.ended_at));
      appendDefinition(grid, t("terminal reason"), attempt.terminal_reason || "—");
      appendDefinition(grid, t("failure code"), attempt.failure_code || "—");
      appendDefinition(grid, t("return code"), attempt.returncode ?? "—");
      appendDefinition(grid, "diagnostic", attempt.diagnostic_summary || "—");
      appendDefinition(grid, t("stdout log"), attempt.stdout_path || "—");
      appendDefinition(grid, t("stderr log"), attempt.stderr_path || "—");
      const devices = list(attempt.gpu_usage).length ? list(attempt.gpu_usage)
        : attempt.gpu_uuid ? [{...attempt, pressure_exceeded: attempt.resource_pressure_exceeded}] : [];
      devices.forEach((device) => {
        appendDefinition(grid, "GPU", device.gpu_uuid);
        appendDefinition(grid, t("minimum free VRAM"), `${device.minimum_free_memory_mib ?? "?"} MiB`);
        appendDefinition(grid, t("latest free VRAM"), `${device.latest_free_memory_mib ?? "?"} MiB`);
        appendDefinition(grid, t("latest utilization"), `${device.latest_utilization_percent ?? "?"}%`);
        if (device.owned_memory) {
          const own = object(device.owned_memory);
          appendDefinition(grid, t("own sampled latest / peak"), `${own.latest_sample_memory_mib ?? "?"} / ${own.sampled_peak_memory_mib ?? "?"} MiB`);
          appendDefinition(grid, t("own sample coverage"), t("{0}; unknown {1} / {2} samples", own.coverage ?? "unknown", own.unknown_sample_count ?? "?", own.sample_count ?? "?"));
        }
        appendDefinition(
          grid,
          t("reserve pressure"),
          device.pressure_exceeded === true
            ? "EXCEEDED"
            : device.pressure_exceeded === false ? t("not exceeded") : "unknown",
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
    if (!container.childElementCount) appendDefinition(container, t("durable facts"), t("none projected"));
  }

  function renderOracle(container) {
    const oracle = object(state.snapshot && state.snapshot.oracle);
    const audit = object(oracle.observation);
    container.replaceChildren();
    container.className = `oracle-detail ${oracle.ok === true ? "ok" : "issue"}`;
    if (audit.state === "pending" || audit.state === "error") {
      container.append(element("strong", "", audit.state === "pending" ? t("Independent oracle: audit pending") : t("Independent oracle: audit unavailable")));
      container.append(element("p", "", t("No current audit result is available. This does not establish integrity or scientific success.")));
      return;
    }
    container.append(element("p", "panel-copy", t("Historical audit: {0}{1}. Audits describe their read and do not establish current integrity.", formatTimestamp(audit.audited_at), audit.refreshing ? t(" · refresh pending") : "")));
    if (oracle.ok === true) {
      container.append(element("strong", "", t("Independent oracle: last audit clean")));
      container.append(element("p", "", t("No integrity issues were projected for this run. CLEAN is not a scientific-success verdict.")));
      return;
    }
    const issues = list(oracle.issues);
    container.append(element("strong", "", t("Independent oracle: {0} issue{1}", issues.length, issues.length === 1 ? "" : "s")));
    const issueList = element("ul", "evidence-list");
    issues.forEach((issue) => {
      const record = object(issue);
      issueList.append(element("li", "", record.message || record.code || compact(issue, 120)));
    });
    if (!issues.length) issueList.append(element("li", "", t("Oracle status is unavailable.")));
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
      const title = element("p", "", t("Hypothesis evidence · assessment {0}", hypothesis.assessment_id || t("not recorded")));
      hypothesisEvidence.append(title);
      const evidenceIds = list(hypothesis.evidence_ids);
      const evidenceList = element("ul", "evidence-list");
      if (evidenceIds.length) {
        evidenceIds.forEach((evidenceId) => evidenceList.append(element("li", "", evidenceId)));
      } else {
        evidenceList.append(element("li", "", t("No evidence IDs projected.")));
      }
      const assessment = hypothesis.assessment;
      if (assessment !== undefined && assessment !== null) {
        let assessmentText;
        if (typeof assessment === "object") {
          try {
            assessmentText = JSON.stringify(assessment);
          } catch (_error) {
            assessmentText = t("Assessment object is not displayable.");
          }
        } else {
          assessmentText = String(assessment);
        }
        evidenceList.append(element("li", "", t("Assessment: {0}", assessmentText)));
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
      (sharedGoal(snapshot) ? t("One shared goal · ") : t("{0}/{1} components · ", componentCount, list(snapshot.components).length)) +
      t("{0}/{1} experts · ", expertCount, list(snapshot.experts).length) +
      t("{0}/{1} Attempts · ", attemptCount, list(snapshot.attempts).length) +
      t("{0}/{1} rendezvous match. Filters affect cards, activity, map nodes, and drawer Attempts.", rendezvousCount, list(snapshot.rendezvous).length);
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
      renderChanged("global-tasks", [snapshot.overview?.global_tasks, snapshot.overview?.unknown_tasks, snapshot.overview?.grounding_sharing, snapshot.overview?.grounding, snapshot.overview?.host_observation?.availability, snapshot.attempts], () => renderGlobalTasks(snapshot));
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
      setText("product-status", t("Observed {0} · {1}", product.run_id, formatTimestamp(product.observed_at)));
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
    announce(t("Showing {0} view", name === "map" ? t("Research Map") : name));
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
    announce(t("Showing {0} topology", mode));
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
    setText("log-connection", t("Connecting to {0} / {1}…", identity, stream));
    socket.onmessage = (event) => {
      if (state.logSocket !== socket) return;
      try {
        const log = JSON.parse(event.data);
        const panel = byId("live-log-text");
        panel.removeAttribute("data-i18n");
        const follow = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 32;
        panel.textContent = log.available ? log.text || t("(empty log)") : t("Log is not available yet; waiting for the Process Attempt.");
        if (follow) panel.scrollTop = panel.scrollHeight;
        setText("log-connection", t("{0} · {1} · {2} bytes{3}", identity, stream, log.size || 0, log.truncated ? t(" · showing bounded tail") : ""));
      } catch (_error) { setText("log-connection", t("Invalid log message; reconnect to retry.")); }
    };
    socket.onclose = () => { if (state.logSocket === socket) setText("log-connection", t("Log connection closed. Select the experiment to reconnect.")); };
    socket.onerror = () => { if (state.logSocket === socket) setText("log-connection", t("Log subscription unavailable; check authentication and experiment state.")); };
  }

  const WORKBENCH_TITLES = () => ({
    skills: t("Expert skill library"), literature: t("Literature"), sessions: t("Sessions & tools"),
    interactions: t("Leader & Debate"), resources: t("Experiments & resources"),
    reliability: t("Execution reliability"), institutional: t("Institutional access"),
  });
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
      ["experiments", t("Experiments"), object(totals.experiments).total, "resources", t("Distinct experiment IDs")],
      ["hypotheses", t("Hypotheses"), totals.hypotheses, "map", t("Accepted hypothesis identities")],
      ["literature", t("Literature"), object(totals.literature).retrieved, "literature", t("{0} PDFs with extracted evidence", displayValue(object(totals.literature).pdf_available))],
      ["skills", t("Expert skill library"), object(totals.skills).count, "skills", t("{0} accepted/bound catalog", object(totals.skills).coverage || "unavailable")],
      ["evolutions", t("Effective evolution"), object(totals.evolutions).changed, "skills", t("{0} completed / {1} failed; changed commits only", displayValue(object(totals.evolutions).completed), displayValue(object(totals.evolutions).failed))],
      ["leader", "Leader", object(totals.leader).started, "interactions", t("{0} active logical occurrences", displayValue(object(totals.leader).active))],
      ["debate", "Debate", object(totals.debate).started, "interactions", t("{0} active logical occurrences", displayValue(object(totals.debate).active))],
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
      button.children[0].textContent = title;
      button.children[2].textContent = note;
    });
    const elapsed = summary.elapsed_seconds;
    const wait = summary.waiting_human_seconds;
    const host = object(object(state.snapshot && state.snapshot.overview).host_observation || summary.host_observation);
    const halt = object(host.last_recorded_exception);
    setText("research-summary-source", t("All-run totals · elapsed {0} · human wait {1} · observed {2}. Host contact: {3}; OPEN is not liveness. {4}Filters and event pagination do not change these totals.", elapsed == null ? "unavailable" : formatDuration(0, elapsed), wait == null ? "unavailable" : formatDuration(0, wait), formatTimestamp(summary.observed_at), host.availability || "unconfirmed", halt.exception_type ? t("Historical Host exception: {0} at {1}; diagnostics/host.jsonl. ", halt.exception_type, halt.timestamp) : ""));
    let costs = byId("command-retry-costs");
    if (!costs) {
      costs = actionButton(t("Command retries / token usage"), () => {
        const current = object(state.snapshot && state.snapshot.overview);
        const usageFields = (value) => Object.fromEntries(Object.entries(object(value))
          .filter(([key]) => !/cost|pricing|usd/i.test(key)));
        const commands = list(current.stages).map((stage) => ({...stage,
          accounting: usageFields(stage.accounting), context_recovery: usageFields(stage.context_recovery)}));
        openLocalDetail("command_accounting", {notice: t("Observed token lower bounds only; reservations and unknown usage are not measured consumption."), commands});
      });
      costs.id = "command-retry-costs";
      container.append(costs);
    }
    if (!byId("stage-token-usage")) {
      const button = actionButton(t("Tokens by stage"), () => {
        const current = object(state.snapshot?.overview || state.product?.summary);
        openLocalDetail("stage_accounting", {stages: current.stage_accounting});
      });
      button.id = "stage-token-usage"; container.append(button);
    }
    if (!byId("execution-reliability")) {
      const button = actionButton(t("Execution reliability"), () => openWorkbench("reliability"));
      button.id = "execution-reliability"; container.append(button);
    }
    setText("command-retry-costs", t("Command retries / token usage"));
    setText("stage-token-usage", t("Tokens by stage"));
    setText("execution-reliability", t("Execution reliability"));
  }

  const RELIABILITY_METRICS = () => ([
    ["output_first_pass", t("First output pass")], ["correction_recovery", t("Correction recovery")],
    ["node_retry", t("Nodes needing retry")], ["node_delivery", t("Final delivery")],
    ["required_actions", t("Required actions")],
  ]);

  const RELIABILITY_SHORT = ["🎯 FP", "🔧 CR", "🔁 RT", "📦 DL", "🧰 RA"];

  function compactReliability(parent, metrics, {role = "", expanded = false, taskScope = "", runGroup = ""} = {}) {
    RELIABILITY_METRICS().forEach(([key, label], index) => {
      const metric = object(object(metrics)[key]);
      const rate = typeof metric.rate === "number" ? `${(metric.rate * 100).toFixed(0)}%` : "—";
      const coverage = [["unknown", "?"], ["excluded", "−"]].filter(([name]) => metric[name] !== 0)
        .map(([name, symbol]) => `${symbol}${metric[name] ?? "?"}`).join(" ");
      const button = actionButton("", () => {
        state.reliabilityMetric = key;
        state.reliabilityFilters = {role: "", owner_role: role, task_scope: taskScope || (role ? "expert" : ""), run_group: runGroup, node: "", model: ""};
        openWorkbench("reliability");
      }, "reliability-mini");
      button.dataset.previewMetric = key;
      button.title = t("{0}{1}: {2}. Cumulative within this run; click for denominators and evidence.", label, key === "node_retry" ? t(" (lower is better)") : "", reliabilityMetricText(metric));
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
    return t("{0}/{1} · {2} · unknown {3} · excluded {4}", displayValue(value.numerator), displayValue(value.denominator), rate, displayValue(value.unknown), displayValue(value.excluded));
  }

  function reliabilityMetrics(parent, metrics, scope = null) {
    RELIABILITY_METRICS().forEach(([key, label]) => {
      const button = actionButton(`${label}: ${reliabilityMetricText(object(metrics)[key])}`, () => {
        state.reliabilityMetric = key;
        if (scope) state.reliabilityFilters = {role: scope.role || "", task_scope: scope.task_scope || "", run_group: scope.run_group || "", owner_role: scope.owner_role || "", node: scope.node || "", model: scope.model || ""};
        renderReliability();
      }, "reliability-metric detail-link");
      button.dataset.reliabilityMetric = key;
      button.setAttribute("aria-pressed", String(state.reliabilityMetric === key));
      parent.append(button);
    });
  }

  function reliabilityCosts(parent, costs) {
    const value = object(costs);
    parent.append(element("p", "reliability-costs panel-copy", t("Retry overhead: {0} extra Attempts · reported ≥{1} tokens · reserve {2} tokens · unavailable token records {3} · unknown closed {4} · elapsed {5}. Correction-only tokens: unavailable. Unknown does not mean zero; reserves are not measured usage.", displayValue(value.retry_attempts), displayValue(value.retry_reported_tokens), displayValue(value.retry_reserved_tokens), displayValue(value.retry_unavailable_token_attempts), displayValue(value.retry_unknown_closed_attempts), value.retry_elapsed_seconds == null ? "unavailable" : formatDuration(0, value.retry_elapsed_seconds))));
    parent.append(element("p", "panel-copy", t("unknown retry nodes {0} · unavailable reserves {1} · unknown elapsed {2} Attempts.", displayValue(value.retry_unknown_nodes), displayValue(value.retry_unavailable_reservations), displayValue(value.retry_elapsed_unknown_attempts))));
  }

  function renderReliability() {
    const source = object(object(state.snapshot && state.snapshot.overview).reliability);
    const groups = list(source.groups), nodes = list(source.nodes);
    const filters = state.reliabilityFilters;
    const container = byId("workbench-list");
    const renderedKey = JSON.stringify([uiLanguage, "reliability", source, filters, state.reliabilityMetric]);
    setText("workbench-scope", t("All-run metrics use recorded denominators. Task ownership, execution role, node and model filters narrow evidence below; they do not recalculate rates."));
    setText("workbench-status", Object.keys(source).length ? t("Execution reliability, not a model capability score. Read-only recorded evidence.") : t("Reliability unavailable for this snapshot; missing evidence is unknown."));
    setText("workbench-page", t("Recorded node evidence; no event-pagination denominator"));
    byId("workbench-previous").disabled = true; byId("workbench-next").disabled = true;
    if (container.dataset.renderedKey === renderedKey) return;
    container.dataset.renderedKey = renderedKey; container.replaceChildren();
    const summary = element("section", "detail-card"); summary.id = "reliability-summary";
    summary.append(element("h3", "", t("All-run execution reliability")));
    reliabilityMetrics(summary, source.metrics);
    reliabilityCosts(summary, source.costs);
    const counts = object(source.counts);
    summary.append(element("p", "panel-copy", t("Timeouts {0} · cancelled {1} · tool errors {2}/{3}{4} · unknown traces {5}. Scientific refutation and expected test failures are not model failures.", displayValue(counts.timed_out_attempts), displayValue(counts.cancelled_attempts), displayValue(counts.tool_errors), displayValue(counts.tool_calls), counts.trace_unknown_attempts === 0 ? "" : t(" (observed lower bounds)"), displayValue(counts.trace_unknown_attempts))));
    list(source.notices).forEach((notice) => summary.append(element("p", "panel-copy", displayValue(notice))));
    container.append(summary);
    const controls = element("div", "filter-grid");
    ["task_scope", "run_group", "owner_role", "role", "node", "model"].forEach((key) => {
      const label = element("label", "", ({role: t("Execution role"), owner_role: t("Owning expert"), task_scope: t("Task scope"), run_group: t("Run task group")})[key] || key);
      const select = element("select"); select.id = `reliability-${key}`;
      const all = element("option", "", key === "run_group" ? t("All run task groups") : t("All {0}s", key)); all.value = ""; select.append(all);
      const values = new Set([...groups, ...nodes].map((row) => row[key] || "unknown"));
      if (filters[key]) values.add(filters[key]);
      [...values].sort().forEach((value) => {
        const text = key === "run_group" ? ({grounding: "Grounding", other: t("Other global tasks"), unknown: t("Unknown")})[value] || value : value;
        const option = element("option", "", text); option.value = value; select.append(option);
      });
      select.value = filters[key] || "";
      select.addEventListener("change", () => { state.reliabilityFilters[key] = select.value; renderReliability(); byId(`reliability-${key}`).focus({preventScroll: true}); });
      label.append(select); controls.append(label);
    });
    controls.append(actionButton(t("All metric evidence"), () => { state.reliabilityMetric = ""; renderReliability(); }));
    container.append(controls);
    const matches = (row) => Object.entries(filters).every(([key, value]) => !value || (row[key] || "unknown") === value);
    const groupList = element("section"); groupList.id = "reliability-groups";
    groups.filter(matches).forEach((group) => {
      const card = element("article", "detail-card");
      card.append(element("h3", "", `${taskScopeLabel(group)} · ${group.node || "unknown"} · ${group.model || "unknown"}`));
      card.append(element("p", "panel-copy", t("Execution role: {0}", group.role || "unknown")));
      reliabilityMetrics(card, group.metrics, group); reliabilityCosts(card, group.costs);
      groupList.append(card);
    });
    if (!groupList.children.length) groupList.append(element("p", "empty-copy", t("No recorded groups match these filters.")));
    container.append(groupList);
    const selectedMetric = state.reliabilityMetric;
    const label = element("h3", "", selectedMetric ? t("{0}: denominator and unknown node evidence", RELIABILITY_METRICS().find(([key]) => key === selectedMetric)[1]) : t("All logical nodes and their Attempts"));
    label.id = "reliability-list-label"; container.append(label);
    const nodeList = element("section"); nodeList.id = "reliability-nodes";
    nodes.filter(matches).filter((node) => {
      const metric = object(node.metrics)[selectedMetric];
      return !selectedMetric || !metric || metric.denominator > 0 || metric.unknown > 0;
    }).forEach((node) => {
      const card = element("article", "detail-card reliability-node");
      card.append(actionButton(`${node.node || "unknown"} · ${node.command_id || t("unknown command")}`, () => openLocalDetail("reliability_node", node), "record-title"));
      card.append(element("p", "panel-copy", t("{0} · execution role {1} · {2} · {3} · {4} recorded Attempts", taskScopeLabel(node), node.role || "unknown", node.model || "unknown", node.status || "unknown", list(node.attempts).length)));
      if (selectedMetric) card.append(element("p", "panel-copy", reliabilityMetricText(object(node.metrics)[selectedMetric])));
      nodeList.append(card);
    });
    if (!nodeList.children.length) nodeList.append(element("p", "empty-copy", t("No recorded nodes match this metric and filter. Excluded nodes do not enter its denominator.")));
    container.append(nodeList);
  }

  function renderReliabilityNode(parent, node) {
    structuredDetail(parent, node, ["attempts", "metrics", "costs"]);
    reliabilityCosts(parent, node.costs);
    list(node.attempts).forEach((attempt) => {
      const card = element("article", "detail-card");
      card.append(element("h4", "", `${attempt.attempt_id} · ${attempt.terminal_reason || "OPEN"}`));
      const attribution = object(attempt.attribution);
      card.append(element("p", "panel-copy", t("Failure: {0} · stage {1} · retry class {2}", attempt.failure_code || t("not recorded"), attempt.failure_stage || "unknown", attempt.retry_class || "unknown")));
      card.append(element("p", "panel-copy", t("Attribution: {0} · confidence {1} · {2}", attribution.category || "unverified", attribution.confidence || "unknown", attribution.reason || t("Cause not established"))));
      const observations = element("ul", "reliability-observations");
      list(attempt.observations).forEach((item) => observations.append(element("li", "panel-copy",
        [String(item.kind || "observation").replaceAll("_", " "), item.code, item.stage, item.name,
          item.count == null ? null : t("count {0}", item.count), item.cause ? t("cause {0}", item.cause) : null].filter((value) => value != null).join(" · "))));
      if (observations.children.length) card.append(observations);
      structuredDetail(card, {corrections: attempt.corrections ?? "unavailable", trace: attempt.trace ?? "unavailable", started_at: attempt.started_at, ended_at: attempt.ended_at});
      if (attempt.attempt_id) card.append(actionButton(t("Open Attempt / tools / corrections"), () => openRemoteDetail("attempt", {attempt_id: attempt.attempt_id}), "detail-link reliability-attempt-link"));
      parent.append(card);
    });
    if (!list(node.attempts).length) parent.append(element("p", "empty-copy", t("Attempt evidence unavailable.")));
  }

  function workbenchQuery() {
    const parameters = {kind: state.workbenchKind, offset: state.workbenchOffset, limit: DETAIL_PAGE_SIZE};
    const role = selectedFilters().expert;
    if (role) parameters.role = role;
    if (state.workbenchKind === "skills") Object.assign(parameters, state.skillFilters);
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
      if (!response.ok) throw new Error(response.status === 401 ? t("Detail access was not authorized.") : t("Detail evidence is unavailable for this selection."));
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
    if (state.workbenchKind === "reliability" || state.workbenchKind === "institutional") { renderWorkbench(); return; }
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
      if (request === state.workbenchRequest) setText("workbench-status", t("{0} Retained rows may be stale.", error.message));
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
    const context = element("p", "panel-copy", `${experiment.role || t("Expert unavailable")} · ${experiment.component_id || t("Component unavailable")}`);
    parent.append(context);
    if (estimate.basis) parent.append(element("p", "resource-basis", t("Estimate basis: {0}", estimate.basis)));
    const attempts = list(experiment.attempts);
    if (!attempts.length) {
      parent.append(element("p", "empty-copy", t("Requested estimate: {0}. No Process Attempt is recorded yet.", memoryLabel(estimate.peak_memory_mib))));
    }
    attempts.forEach((attempt) => {
      const process = element("section", "resource-process");
      const heading = element("div", "resource-process-heading");
      heading.append(element("h4", "", t("Process · {0}", attempt.attempt_id)), element("span", `counter ${statusClass(attempt.state)}`, `${attempt.state || "unavailable"} · ${attempt.terminal_reason || t("terminal result unavailable")}`));
      process.append(heading);
      const measured = object(attempt.measured);
      const metrics = element("dl", "resource-metrics");
      resourceMetric(metrics, t("Requested estimate"), memoryLabel(estimate.peak_memory_mib));
      resourceMetric(metrics, t("Admitted workload"), memoryLabel(attempt.admitted));
      const qualifiedPeak = measured.measurement_status === "complete" && typeof measured.sample_peak_bytes === "number"
        ? memoryLabel(Math.ceil(measured.sample_peak_bytes / 1048576)) : "unknown";
      resourceMetric(metrics, t("Qualified measurement"), qualifiedPeak, qualifiedPeak === "unknown" ? "resource-unknown" : "");
      process.append(metrics);
      const grant = object(attempt.grant);
      const granted = list(grant.gpu_uuids).length ? grant.gpu_uuids : grant.gpu_uuid ? [grant.gpu_uuid] : [];
      process.append(element("p", "resource-device", t("Granted GPU UUID: {0}", granted.join(" · ") || (grant.kind === "CPU" ? t("CPU only") : "unavailable"))));
      const devices = list(attempt.usage);
      devices.forEach((device) => {
        const owned = object(device.owned_memory);
        const samples = element("dl", "resource-metrics resource-samples");
        if (devices.length > 1) process.append(element("p", "resource-device", t("Observed device: {0}", device.gpu_uuid)));
        resourceMetric(samples, t("Own sampled peak"), memoryLabel(owned.sampled_peak_memory_mib));
        resourceMetric(samples, "Samples / unknown", `${displayValue(owned.sample_count)} / ${displayValue(owned.unknown_sample_count)}`, owned.unknown_sample_count === 0 ? "" : "resource-unknown");
        resourceMetric(samples, "Coverage", owned.coverage || "unknown", owned.coverage === "complete" ? "" : "resource-unknown");
        process.append(samples);
      });
      if (!devices.length) process.append(element("p", "panel-copy", t("Per-device sampled peak, sample count and coverage are unavailable.")));
      parent.append(process);
    });
    const sources = element("section", "resource-sources");
    sources.append(element("h4", "", t("Used by later estimates")));
    const next = list(experiment.next_estimate_sources);
    next.forEach((source) => {
      const link = actionButton(source.experiment_id, () => openRemoteDetail("resources", {experiment_id: source.experiment_id}), "detail-link resource-next-link");
      sources.append(link, element("p", "panel-copy", t("Measurement source: {0}", list(source.measurement_attempt_ids).join(", ") || "unavailable")));
    });
    if (!next.length) sources.append(element("p", "panel-copy", t("No later experiment cites these measurements yet.")));
    parent.append(sources);
    const inputSources = list(estimate.measurement_attempt_ids);
    if (inputSources.length) {
      const references = element("p", "panel-copy", t("This estimate cites: {0}", inputSources.join(", ")));
      parent.append(references);
    }
    if (includeRaw) {
      parent.append(element("p", "panel-copy", t("Owned sampled peaks remain separate from qualified measurements. Unknown samples prevent a complete measurement; device occupancy is not attributed experiment memory.")));
      const raw = element("details", "resource-raw");
      raw.append(element("summary", "", t("Raw accepted resource evidence")), element("pre", "detail-code", displayValue(experiment)));
      parent.append(raw);
    }
  }

  function renderDownloadEvidence(parent, paper) {
    const evidence = element("details", "download-evidence");
    const attempts = list(paper.download_attempts);
    evidence.append(element("summary", "", paper.download_coverage === "recorded"
      ? t("Download evidence · {0} recorded Attempt{1}", attempts.length, attempts.length === 1 ? "" : "s")
      : t("Download history unavailable · older evidence is unknown")));
    attempts.forEach((attempt) => {
      const row = element("article", "detail-card");
      row.append(element("h4", "", `${attempt.role || t("Role unavailable")} · ${attempt.attempt_id}`),
        element("p", "", t("Recorded disposition: {0}", attempt.retrieval_disposition || "unavailable")));
      if (attempt.retrieval_disposition === "PDF_UNAVAILABLE" && list(attempt.events).some((event) => event.outcome === "EXTRACTION_FAILURE")) {
        row.append(element("p", "panel-copy", t("PDF downloaded, but evidence extraction failed. This paper has no accepted full-text evidence.")));
      }
      if (list(attempt.events).some((event) => ["landing_uri", "source_uri"].includes(event.stage) && event.outcome === "VALIDATION_FAILURE")) {
        row.append(element("p", "panel-copy", t("Source address rejected. Metadata retained; this Attempt supplied no full-text evidence for this paper.")));
      }
      const events = element("ul", "evidence-list");
      list(attempt.events).forEach((event) => events.append(element("li", "",
        [event.stage, event.outcome, event.source_host, event.failure_code, event.http_status ? `HTTP ${event.http_status}` : null].filter(Boolean).join(" · "))));
      if (!list(attempt.events).length) events.append(element("li", "", t("No download steps retained for this Attempt.")));
      row.append(events);
      if (attempt.attempt_id) row.append(actionButton(t("Inspect source Attempt"), () => openRemoteDetail("attempt", {attempt_id: attempt.attempt_id})));
      evidence.append(row);
    });
    parent.append(evidence);
  }

  function paperSourceLink(parent, label, value, paper) {
    let host;
    try { host = new URL(value).hostname.toLowerCase().replace(/\.$/, ""); } catch (_) { return; }
    const rejected = list(paper.download_attempts).some((attempt) => list(attempt.events).some((event) =>
      ["landing_uri", "source_uri"].includes(event.stage) && event.outcome === "VALIDATION_FAILURE" && event.source_host === host));
    if (rejected) parent.append(element("p", "panel-copy", t("Source link withheld: this host has a recorded address rejection. See download history.")));
    else safeExternalLink(parent, label, value);
  }

  function detailCard(kind, item) {
    const card = element("article", "detail-card");
    const grid = element("dl", "selection-grid");
    const field = (name, value) => appendDefinition(grid, name, displayValue(value));
    if (kind === "skills") {
      card.append(actionButton(item.name || t("Unnamed skill"), () => openRemoteDetail("skill", {role: item.role, name: item.name, commit: item.commit}), "record-title"));
      field("expert", item.role); field(t("accepted / bound version"), item.commit);
      field(t("current accepted / bound"), item.current); field("description", item.description);
      field(t("request bindings"), list(item.bindings).length);
      const usage = object(item.usage);
      field(t("Observed calls"), usage.calls === undefined ? "unavailable" : `${usage.coverage === "complete" ? "" : "≥"}${usage.calls}`);
      field(t("Loaded / failed / unanswered"), [usage.succeeded, usage.failed, usage.unanswered].map(displayValue).join(" / "));
      if (usage.last_called_at) field(t("Last call"), formatTimestamp(usage.last_called_at));
      card.append(element("p", "panel-copy", t("Publication and request binding do not prove a native Skill was loaded. Open adoption evidence for actual loads.")));
    } else if (kind === "literature") {
      card.append(actionButton(item.title || item.bibliographic_identity || t("Literature record"), () => openLocalDetail("literature", item), "record-title"));
      field("identity", item.bibliographic_identity || item.identity); field("experts", item.roles);
      field("year / venue", `${displayValue(item.year || item.publication_year)} / ${displayValue(item.venue)}`);
      field(t("PDF evidence"), item.retrieval_disposition ?? item.pdf_available ?? item.pdf_status ?? "unavailable");
      if (item.pdf_available_roles) field(t("PDF available to"), item.pdf_available_roles);
      field(t("accepted reviews"), item.review_attempt_ids || item.accepted_review_attempt_ids || "unavailable");
      paperSourceLink(card, "Paper / DOI ↗", item.landing_uri || item.url || item.doi_url || (item.doi ? `https://doi.org/${item.doi}` : null), item);
      paperSourceLink(card, t("PDF / retrieval source ↗"), item.source_uri || item.pdf_url, item);
      renderDownloadEvidence(card, item);
    } else if (kind === "sessions") {
      card.append(element("h3", "", item.session_id || t("Session identity unavailable")));
      field(t("task ownership"), taskScopeLabel(item)); field(t("execution role"), item.role); field("component", item.component_id);
      field("scope / chain", `${displayValue(item.scope)} / ${displayValue(item.chain)}`);
      field("cycle / branch", `${displayValue(item.cycle)} / ${displayValue(item.branch)}`);
      field(t("observed launch modes"), list(item.launch_modes).join(" → ") || "unknown");
      card.append(grid);
      if (item.parent_session_id) card.append(actionButton(t("Parent session: {0}", item.parent_session_id),
        () => openWorkbench("sessions", {session_id: item.parent_session_id}), "detail-link session-parent"));
      else card.append(element("p", "panel-copy", t("Parent session: not recorded.")));
      list(item.attempts).forEach((attempt) => {
        const button = actionButton("", () => openRemoteDetail("attempt", {attempt_id: attempt.attempt_id}), "session-attempt");
        const mode = ["new", "fork", "resume"].includes(attempt.launch_mode) ? attempt.launch_mode : "unknown";
        button.append(element("span", "session-attempt-name", attempt.command || attempt.phase || t("Attempt")),
          element("span", "counter session-launch", mode), element("span", "panel-copy", `${attempt.state} · ${attempt.attempt_id}`));
        card.append(button);
      });
      if (!list(item.attempts).length) card.append(element("p", "panel-copy", t("No attempts in this session page.")));
      return card;
    } else if (kind === "interactions") {
      card.append(actionButton(`${item.kind} · ${item.interaction_id || item.command_id || "interaction"}`, () => openLocalDetail("interaction", item), "record-title"));
      field("state", item.state); field("participants", item.participants);
      if (item.kind === "leader") {
        field("mode", item.mode === "experience_sharing" ? t("Experience sharing · peers continue independently") : t("Retained exclusive round"));
        if (item.guidance) {
          field(t("advice author"), item.guidance.author); field("advice", item.guidance.summary);
          field(t("supporting inputs"), item.guidance.supporting_refs);
          field(t("provided to proposals"), item.provided_to);
          card.append(element("p", "panel-copy", t("Provided means included in the recorded task input. It does not establish adoption, understanding or scientific validation.")));
        }
      }
      field("verdict", item.verdict); field(t("source Attempts"), item.source_attempt_ids);
      card.append(element("p", "panel-copy", t("Aborted coordination is closed control work; it does not create a scientific refutation.")));
    } else if (kind === "resources") {
      card.append(actionButton(item.experiment_id, () => openLocalDetail("resource", item), "record-title"));
      renderResourceTrace(card, item);
      card.append(actionButton(t("Open experiment logs"), () => { setActiveView("activity"); openExperimentLog(item.experiment_id); }));
    }
    card.append(grid);
    return card;
  }

  const LITERATURE_STATE_LABELS = () => ({
    queued: t("Queued for acquisition"), checking: t("Checking"), login_required: t("Browser login required"),
    verification_required: t("Browser verification required"), browser_unavailable: t("Browser disconnected"),
    fetch_unavailable: t("Load failed; awaiting automatic retry"), access_required: t("Subscription or institutional access required"),
    available: t("Full text ready"), access_denied: t("Current institution has no access"), no_full_text: t("No full text found"),
    identity_unconfirmed: t("Paper identity unconfirmed"), failed: t("File acquisition or parsing failed"),
    sample_verified: t("Sample full text verified"), opened: t("Preparation page opened; log in in the browser"),
    unchecked: t("Not checked"), tab_limit: t("Browser tab limit reached; prepare in batches"), skipped: t("Paper skipped"), public_only: t("Public/OA only"), disabled: t("Institutional route not enabled"), queue_timeout: t("Queue timed out"),
    acquisition_timeout: t("Acquisition timed out"), manual_timeout: t("Active login wait ended"),
  });
  const LITERATURE_REASON_LABELS = () => ({
    network_changed: t("Network changed"), network_error: t("Network error"), dns_failed: t("DNS resolution failed"),
    connection_failed: t("Connection failed"), navigation_timeout: t("Load timed out"), redirect_unresolved: t("Redirect unresolved"),
    http_server_error: t("Publisher temporarily unavailable"), http_rate_limited: t("Publisher rate limited"),
    request_blocked: t("Request blocked"), http_error: t("HTTP request failed"),
    pdf_response_not_pdf: t("PDF endpoint returned non-PDF content"),
  });

  function literatureWritable(access) {
    const status = object(state.snapshot && state.snapshot.status);
    return Boolean(literatureToken) && Boolean(object(access).enabled)
      && !["STOPPING", "COMPLETED", "FAILED", "CANCELLED"].includes(status.run_state)
      && !state.literatureBusy;
  }

  async function requestLiterature(providerName, action, url, path, mode) {
    const access = object(object(state.snapshot && state.snapshot.status).literature_access);
    if (!literatureWritable(access)) return;
    if (action === "import" && !String(path || "").startsWith("/")) {
      state.literatureMessage = t("Import requires an absolute server-side PDF path.");
      renderInstitutional();
      return;
    }
    const body = {provider: providerName, action};
    if (url) body.url = url;
    if (path) body.path = path;
    if (mode) body.mode = mode;
    const choiceKey = JSON.stringify(body);
    const durable = ["skip", "restore", "mode"].includes(action);
    if (durable) {
      if (state.literatureUnconfirmed?.key === choiceKey) Object.assign(body, state.literatureUnconfirmed.body);
      else {
        body.request_id = `web-literature-${crypto.randomUUID()}`;
        body.expected_revision = object(state.snapshot && state.snapshot.status).run_revision;
        state.literatureUnconfirmed = {key: choiceKey, body: {...body}};
      }
    }
    state.literatureBusy = true;
    state.literatureMessage = action === "open" ? t("Requesting a login tab…") : action === "retry" ? t("Scheduling a retry…") : action === "verify" ? t("Verifying sample…") : t("Submitting operation…");
    renderInstitutional();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch("/api/literature-access", {method: "POST", cache: "no-store", signal: controller.signal,
        headers: {Authorization: `Bearer ${accessToken}`, "X-Hypoloom-Literature-Token": literatureToken, "Content-Type": "application/json"},
        body: JSON.stringify(body)});
      const result = await response.json();
      if (result.status === "accepted") {
        if (durable) state.literatureUnconfirmed = null;
        state.literatureMessage = durable ? t("Preferences saved for this run; public sources and admitted documents remain available.") : action === "open"
          ? t("Requested a tab in the connected browser. Complete login there; checks and retries continue while other research proceeds.")
          : action === "retry" ? t("Check scheduled. After full-text validation, research Agents can read it through the document tool.")
          : action === "verify" ? t("Sample verification scheduled; it proves only this paper's current readability and does not enter the research archive.") : t("PDF import scheduled. It becomes available to research Agents after file and paper-identity validation.");
      } else if (["rejected", "conflict"].includes(result.status) || [400, 403].includes(response.status)) {
        if (durable) state.literatureUnconfirmed = null;
        state.literatureMessage = t("Not accepted: {0}", result.error?.message || result.message || t("Check operator access and run state"));
      } else throw new Error("unconfirmed");
    } catch (_error) {
      state.literatureMessage = t("Host reply unconfirmed. Refresh before retrying; completed research results remain unchanged.");
    } finally {
      clearTimeout(timer);
      state.literatureBusy = false;
      renderInstitutional();
    }
  }

  function renderInstitutional() {
    const status = object(state.snapshot && state.snapshot.status);
    const access = object(status.literature_access);
    const groups = list(access.groups);
    const container = byId("workbench-list");
    setText("workbench-scope", t("Public/OA retrieval is available for all publishers, including those not listed here. Institutional settings below do not guarantee login, subscription or successful downloads."));
    const inactiveMessage = "enabled" in access
      ? t("Institutional browser not enabled for this run. Public/OA retrieval remains available.")
      : t("Institutional status unknown: no current Host observation.");
    setText("workbench-status", state.literatureMessage || (access.enabled
      ? t("Institutional queue: {0} pending · {1} ready. Excludes public downloads and history.", displayValue(access.pending), displayValue(access.available))
      : inactiveMessage));
    setText("workbench-page", t("Institutional full-text requests · no pagination"));
    byId("workbench-previous").disabled = true; byId("workbench-next").disabled = true;
    const renderedKey = JSON.stringify([uiLanguage, "institutional", access, state.literatureMessage, state.literatureBusy]);
    if (container.dataset.renderedKey === renderedKey) return;
    container.dataset.renderedKey = renderedKey;
    container.replaceChildren();
    if (!access.enabled) {
      container.append(element("p", "empty-copy", inactiveMessage));
      return;
    }
    if (!literatureToken) {
      container.append(element("p", "empty-copy", t("Read-only list. Actions require an operator link from Web launched with --allow-literature-control.")));
    }
    let endpointPort = "";
    try { endpointPort = String(new URL(String(access.browser_endpoint || "")).port || ""); } catch (_error) { /* endpoint unavailable */ }
    const guide = element("section", "detail-card institutional-guide");
    let serverUi = null;
    try {
      const candidate = new URL(access.browser_ui_url);
      if (["http:", "https:"].includes(candidate.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(candidate.hostname)
          && candidate.port && !candidate.username && !candidate.password && !candidate.search && !candidate.hash
          && candidate.pathname === "/") serverUi = candidate;
    } catch (_error) { /* laptop browser or invalid GUI origin */ }
    if (access.connection) {
      const connection = access.connection;
      guide.append(element("p", "", t("Connections are allocated per run and released when it ends; the dedicated browser profile is retained.")));
      const instructions = {
        client: t("Keep the Windows connector running. It discovers the run and opens a tunnel automatically; no server port entry is needed. Then select a publisher and Open login; complete institution login in the browser."),
        local: t("On the Linux desktop running the project, select a publisher and Open login. Use the dedicated browser that opens. Only one run can use the same profile at a time."),
        server: t("In a server terminal, run hypoloom institutional connections --state-root <state-root> --include-ui-credentials to obtain this run's GUI entry and credentials, then forward the displayed GUI port. Enter institution credentials only in the browser."),
        existing: t("Log in in the configured external browser; the project releases only its own run connection. You continue to manage the external browser.")
      };
      guide.append(element("p", "", instructions[connection.mode] || t("See the institutional access guide.")));
      guide.append(element("p", "", t("Connection state: {0}. This does not verify institution subscription rights.", displayValue(connection.state))));
      if (connection.reason) guide.append(element("p", "", connection.reason === "port_in_use"
        ? t("The selected fixed port is occupied. Release it before retrying; other research continues.") : t("Connection resources are temporarily unavailable. Retry later; other research continues.")));
    } else if (serverUi) {
      guide.append(element("p", "", t("Server browser: login state stays on the server and may be reused for later papers and new runs while valid.")));
      guide.append(element("p", "", t("Only for first login or an expired session, run on your computer: ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:{0}:{1}:{2} <ssh-user>@<research-server>", serverUi.port, serverUi.hostname, serverUi.port)));
      const openBrowser = element("a", "detail-link", t("Open server browser to log in to your institution"));
      openBrowser.href = `${serverUi.protocol}//127.0.0.1:${serverUi.port}/`;
      openBrowser.target = "_blank"; openBrowser.rel = "noopener noreferrer";
      guide.append(openBrowser, element("p", "", t("After login, you may disconnect SSH and close your laptop; the server continues downloading. Enter credentials only in the remote browser. This panel does not collect them.")));
    } else {
    guide.append(element("h3", "", t("Connect the browser before login (run locally on a computer on your institution network)")));
    guide.append(element("pre", "panel-copy", access.windows_setup_command || t("Start dedicated Chrome/Edge using its actual installation path and a separate --user-data-dir; see the institutional access guide.")));
    guide.append(element("p", "panel-copy", t("2) On the same computer, open an SSH reverse tunnel: ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 127.0.0.1:{0}:127.0.0.1:{1} <ssh-user>@<research-server>", endpointPort || t("<port>"), endpointPort || t("<port>"))));
    guide.append(element("p", "panel-copy", t("3) Select Open login, then complete institution SSO/MFA in the browser. Enter credentials and codes only there; this panel does not collect them.")));
    if (!endpointPort) guide.append(element("p", "panel-copy", t("Use the port in the Host --institutional-browser-url argument; replace <port> in the commands above.")));
    }
    container.append(guide);
    if (!groups.length) {
      container.append(element("p", "empty-copy", t("No institutional full-text requests are pending.")));
      return;
    }
    const writable = literatureWritable(access);
    groups.forEach((group) => {
      const providerName = group.provider;
      const section = element("section", "detail-card institutional-group");
      const heading = element("div", "institutional-heading");
      heading.append(element("h3", "", displayValue(providerName)));
      const open = actionButton(t("Open login"), () => requestLiterature(providerName, "open", "", ""), "detail-link");
      const retry = actionButton(t("Check and retry"), () => requestLiterature(providerName, "retry", "", ""), "detail-link");
      open.disabled = !writable; retry.disabled = !writable;
      const modeButton = actionButton(group.mode !== "auto" ? t("Enable institutional access") : t("Public/OA only"),
        () => requestLiterature(providerName, "mode", "", "", group.mode !== "auto" ? "auto" : "public_only"), "detail-link");
      modeButton.disabled = !writable;
      open.disabled ||= group.mode !== "auto"; retry.disabled ||= group.mode !== "auto";
      heading.append(open, retry, modeButton);
      section.append(element("p", "panel-copy", t("Institutional route: {0} · active wait: {1} seconds remaining", ({auto: t("Allowed"), public_only: t("Off (public only)"), disabled: t("Not enabled")})[group.mode] || t("Policy unknown"), group.manual_remaining_seconds || 0)));
      section.append(heading);
      const prep = object(group.preparation);
      section.append(element("p", "panel-copy", `${LITERATURE_STATE_LABELS()[prep.state] || prep.state || t("Not checked")}${prep.last_checked_at ? " · " + prep.last_checked_at : ""}`));
      const sample = element("input", "institutional-sample");
      sample.placeholder = t("Sample DOI or canonical article URL");
      const sampleKey = t("sample {0}", providerName);
      sample.value = state.literatureDraft.get(sampleKey) || "";
      sample.addEventListener("input", () => state.literatureDraft.set(sampleKey, sample.value));
      const verify = actionButton(t("Verify sample"), () => {
        let url = (state.literatureDraft.get(sampleKey) || "").trim().replace(/^doi:/i, "");
        if (url.startsWith("10.")) url = "https://doi.org/" + url;
        requestLiterature(providerName, "verify", url);
      }, "detail-link");
      verify.disabled = !writable || group.mode !== "auto";
      section.append(sample, verify);
      list(group.papers).forEach((paper) => {
        const row = element("div", "institutional-paper");
        const title = element("div", "institutional-paper-title");
        const label = paper.title || paper.url || t("Paper unavailable");
        let linked = false;
        try {
          const parsed = new URL(String(paper.url));
          if (["https:", "http:"].includes(parsed.protocol)) {
            const link = element("a", "detail-link", label);
            link.href = parsed.href; link.target = "_blank"; link.rel = "noopener noreferrer";
            title.append(link);
            linked = true;
          }
        } catch (_error) { /* fall back to plain text */ }
        if (!linked) title.append(element("span", "", label));
        const stateLabel = LITERATURE_STATE_LABELS()[paper.state] || paper.state || t("Unknown");
        row.append(title, element("span", `counter${paper.state === "available" ? " institutional-ready" : ""}`, stateLabel));
        if (paper.reason_code) {
          const reason = LITERATURE_REASON_LABELS()[paper.reason_code];
          row.append(element("span", "", t("Reason: {0}", reason ? `${reason}（${paper.reason_code}）` : paper.reason_code)));
        }
        if (Number.isFinite(paper.retry_in_seconds)) row.append(element("span", "", t("Next check in about {0} seconds", paper.retry_in_seconds)));
        if (paper.last_checked_at) row.append(element("span", "", t("Last checked: {0}", formatTimestamp(Date.parse(paper.last_checked_at) / 1000))));
        const draftKey = `${providerName} ${paper.url}`;
        const importRow = element("div", "institutional-import");
        const input = element("input", "institutional-import-path");
        input.type = "text";
        input.placeholder = t("Absolute path to a PDF already on the server (for import)");
        input.value = state.literatureDraft.get(draftKey) || "";
        input.addEventListener("input", () => state.literatureDraft.set(draftKey, input.value));
        const importButton = actionButton(t("Import PDF"), () => {
          requestLiterature(providerName, "import", paper.url || "", (state.literatureDraft.get(draftKey) || "").trim());
        }, "detail-link");
        importButton.disabled = !writable;
        const skipButton = actionButton(paper.skipped ? t("Resume paper") : t("Skip paper"),
          () => requestLiterature(providerName, paper.skipped ? "restore" : "skip", paper.url), "detail-link");
        skipButton.disabled = !writable;
        importRow.append(input, importButton, skipButton);
        row.append(importRow);
        section.append(row);
      });
      container.append(section);
    });
  }

  function renderSkillCalls(parent, calls, truncated) {
    if (truncated) parent.append(element("p", "panel-copy", t("Only the latest 200 observed calls are listed; totals include all inspected calls.")));
    list(calls).forEach((call) => {
      const row = element("div", "skill-call");
      row.append(element("span", "", `${call.timestamp ? formatTimestamp(call.timestamp) : t("Time unavailable")} · ${call.command || "—"} · ${call.status}`));
      row.append(actionButton(call.attempt_id, () => openRemoteDetail("attempt", {attempt_id: call.attempt_id})));
      parent.append(row);
    });
    if (!list(calls).length) parent.append(element("p", "panel-copy", t("No calls observed in the retained capture. This does not prove the skill was unused.")));
  }

  function renderSkillEvolution(parent, events) {
    const labels = {running: t("In progress"), failed: t("Failed"), unknown: t("Evidence unavailable"),
      unchanged: t("Completed without changes"), accepted_change: t("Accepted change")};
    list(events).forEach((event) => {
      const row = element("article", "skill-evolution detail-card");
      row.append(element("h4", "", `${event.role} · ${labels[event.status] || event.status}`));
      row.append(element("p", "panel-copy", `${formatTimestamp(event.ended_at || event.started_at)} · ${event.command} · ${t("Cycle {0}", event.cycle ?? "—")}`));
      if (event.base_commit && event.commit) row.append(element("p", "panel-copy", `${compact(event.base_commit, 12)} → ${compact(event.commit, 12)}`));
      list(event.skills).forEach((skill) => {
        const section = element("details", "skill-change");
        section.append(element("summary", "", `${skill.name} · ${t("{0} changed files", list(skill.files).length)}`));
        list(skill.files).forEach((file) => section.append(element("p", "panel-copy", `${file.change} · ${file.path}`)));
        section.append(actionButton(t("View skill version"), () => openRemoteDetail("skill", {
          role: event.role, name: skill.name, commit: event.commit,
        })));
        row.append(section);
      });
      if (event.changes_available === false && event.status === "accepted_change") row.append(element("p", "panel-copy", t("Accepted version recorded; changed files unavailable.")));
      row.append(element("p", "panel-copy", t("{0} later bindings · ≥{1} observed later calls in this selection", event.later_bindings || 0, event.observed_later_calls || 0)));
      row.append(actionButton(t("Inspect evolution session"), () => openRemoteDetail("attempt", {attempt_id: event.attempt_id})));
      parent.append(row);
    });
    if (!list(events).length) parent.append(element("p", "panel-copy", t("No evolution attempts in this selection.")));
  }

  function renderSkillLibrary(data) {
    const container = byId("skill-library");
    const activity = object(data.activity);
    const key = JSON.stringify([uiLanguage, activity, state.skillFilters]);
    if (container.dataset.renderedKey === key) return;
    const focus = container.contains(document.activeElement) ? document.activeElement.id : null;
    container.dataset.renderedKey = key;
    container.replaceChildren();
    const controls = element("div", "skill-library-controls");
    const stages = ["", "GROUNDING", "LITERATURE_REVIEW", "PROPOSE_HYPOTHESIS", "SYNTHESIS",
      "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS", "CYCLE_REPORT", "SELF_EVOLVE", "GRAPH_EVOLVE"];
    [["window", t("Time range"), [["", t("Entire run")], ["3600", t("Last hour of run")], ["21600", t("Last 6 hours of run")], ["86400", t("Last 24 hours of run")]]],
     ["command", t("Research stage"), stages.map((stage) => [stage, stage || t("All stages")])]].forEach(([name, label, options]) => {
      const field = element("label", "", label);
      const select = element("select"); select.id = `skills-${name}`;
      options.forEach(([value, text]) => { const option = element("option", "", text); option.value = value; select.append(option); });
      select.value = state.skillFilters[name];
      select.addEventListener("change", () => { state.skillFilters[name] = select.value; state.workbenchOffset = 0; loadWorkbench(); });
      field.append(select); controls.append(field);
    });
    controls.append(actionButton(t("All experts"), () => { byId("filter-expert").value = ""; openWorkbench("skills"); }));
    container.append(controls);
    const coverage = activity.coverage || "unavailable";
    container.append(element("p", "panel-copy skill-coverage", t("{0} · {1} observed calls · complete capture for {2}/{3} sessions · {4} untimed calls", coverage, activity.observed_calls ?? "?", object(activity.capture).complete ?? 0, activity.attempts ?? "?", activity.untimed_calls ?? "?")));
    container.append(element("p", "panel-copy", t("Partial capture gives lower bounds. Binding is not a call; loading is not proof of following a skill or improving results.")));
    if (activity.unmatched_calls) container.append(element("p", "panel-copy", t("{0} calls could not be assigned to a bound skill version.", activity.unmatched_calls)));
    const charts = element("div", "skill-charts");
    const frequency = element("section", "detail-card");
    frequency.append(element("h3", "", t("Most frequently called skills")));
    const ranked = list(activity.skills).slice(0, 20);
    const maximum = Math.max(1, ...ranked.map((item) => item.calls || 0));
    ranked.forEach((item) => {
      const row = element("div", "skill-frequency");
      row.append(actionButton(`${item.role} · ${item.name}`, () => item.entrypoint_available === false
        ? openRemoteDetail("attempt", {attempt_id: item.last_attempt_id})
        : openRemoteDetail("skill", {role: item.role, name: item.name, commit: item.last_called_commit})));
      const track = element("div", "skill-frequency-track");
      const bar = element("span", "skill-frequency-bar"); bar.style.width = `${100 * item.calls / maximum}%`; track.append(bar);
      row.append(track, element("span", "", `${coverage === "complete" ? "" : "≥"}${item.calls}`));
      frequency.append(row);
    });
    if (!ranked.length) frequency.append(element("p", "panel-copy", t("No calls observed in the retained capture. This does not prove the skill was unused.")));
    const trend = element("section", "detail-card");
    trend.append(element("h3", "", t("Calls over time")));
    trend.append(element("p", "panel-copy", t("Calls per {0}-second interval; untimed calls are excluded.", activity.bin_seconds ?? "?")));
    const plot = element("div", "skill-trend"); plot.setAttribute("aria-label", t("Calls over time"));
    const caption = element("p", "panel-copy skill-trend-caption", t("Select a bar to inspect its interval."));
    const peak = Math.max(1, ...list(activity.timeline).map((bin) => bin.calls || 0));
    list(activity.timeline).forEach((bin) => {
      const label = `${formatTimestamp(bin.started_at)} – ${formatTimestamp(bin.ended_at)} · ${t("{0} calls / {1} failures", bin.calls, bin.failed)}`;
      const bar = actionButton("", () => { caption.textContent = label; }, "skill-trend-bar");
      bar.style.height = `${Math.max(3, 100 * bin.calls / peak)}%`;
      bar.dataset.calls = bin.calls; bar.title = label; bar.setAttribute("aria-label", label);
      bar.addEventListener("focus", () => { caption.textContent = label; });
      plot.append(bar);
    });
    trend.append(plot, caption); charts.append(frequency, trend); container.append(charts);
    const evolution = element("details", "skill-evolution-list");
    evolution.open = true;
    evolution.append(element("summary", "", t("Evolution history")));
    evolution.append(element("p", "panel-copy", t("Accepted changes describe file updates, not demonstrated improvements. The stage filter applies to calls; evolution history uses the expert and time filters.")));
    renderSkillEvolution(evolution, activity.evolutions);
    if (activity.evolutions_truncated) evolution.append(element("p", "panel-copy", t("Only the latest 200 evolution attempts are shown.")));
    container.append(evolution);
    if (focus) byId(focus)?.focus({preventScroll: true});
  }

  function renderWorkbench() {
    const kind = state.workbenchKind;
    const data = object(state.workbenchData);
    byId("workbench-list").classList.toggle("execution-reliability", kind === "reliability");
    setText("workbench-title", WORKBENCH_TITLES()[kind]);
    document.querySelectorAll("[data-workbench]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.workbench === kind)));
    byId("skill-library").hidden = kind !== "skills";
    if (kind === "skills") renderSkillLibrary(data);
    if (kind === "reliability") { renderReliability(); return; }
    if (kind === "institutional") { renderInstitutional(); return; }
    const filters = selectedFilters();
    const context = state.workbenchContext;
    const scope = kind === "sessions" && context.task_scope ? t("{0} tasks; expert/component filters retained for other views", taskScopeLabel(context))
      : kind === "sessions" && context.session_id ? t("Exact session {0}; expert/component filters retained for other views", context.session_id)
      : `${filters.expert || t("All experts")}${context.component_id ? t(" · component {0}", context.component_id) : ""}`;
    setText("workbench-scope", t("{0}. Search narrows the current page; totals count the complete run.", scope));
    const all = list(data[WORKBENCH_KEYS[kind]] || data.items);
    const items = all.filter((item) => !filters.search || searchable(item).includes(filters.search));
    const container = byId("workbench-list");
    // The evidence payload is already bounded by the server; do not rebuild unchanged rows on polls.
    const pool = object(data.shared_pool);
    const renderedKey = JSON.stringify([uiLanguage, kind, items, filters.expert, filters.search, pool.devices, pool.available, pool.reason]);
    if (container.dataset.renderedKey !== renderedKey) {
      container.dataset.renderedKey = renderedKey;
      container.replaceChildren();
      if (kind === "resources" && pool.available === true) {
        const capacity = element("article", "detail-card pool-capacity");
        capacity.append(element("h3", "", t("Authorized shared capacity")), element("p", "panel-copy", t("Aggregate device capacity from the explicitly configured pool. No other run's private research is exposed.")));
        list(pool.devices).forEach((device) => {
          const row = element("div", "resource-trace");
          const grid = element("dl", "selection-grid");
          appendDefinition(grid, "GPU", device.gpu_uuid);
          appendDefinition(grid, t("free / allocatable MiB"), `${displayValue(device.free_memory_mib)} / ${displayValue(device.allocatable_memory_mib)}`);
          appendDefinition(grid, t("outstanding / reserve MiB"), `${displayValue(device.outstanding_memory_mib)} / ${displayValue(device.reserve_memory_mib)}`);
          appendDefinition(grid, t("active / unknown attribution"), `${displayValue(device.active_commitments)} / ${displayValue(device.unknown_attribution_commitments)}`);
          row.append(grid); capacity.append(row);
        });
        if (pool.truncated) capacity.append(element("p", "panel-copy", t("Device list truncated at the observation limit.")));
        container.append(capacity);
      }
      items.forEach((item) => container.append(detailCard(kind, item)));
      if (!items.length) container.append(element("p", "empty-copy", state.workbenchData ? t("No matching records in this page. Missing retained evidence remains unavailable.") : t("Loading selected evidence…")));
    }
    const available = data.available === false ? "unavailable" : data.coverage || t("read-only evidence");
    let note = t("{0} · {1} matching rows on this page.", available, items.length);
    if (data.unknown_versions) note += t(" {0} plugin versions unavailable.", data.unknown_versions);
    if (kind === "resources") note += t(" Shared pool: {0}.", pool.available ? t("observed {0}", formatTimestamp(pool.sampled_at)) : pool.reason || t("unavailable; no live pool observation"));
    setText("workbench-status", note);
    const total = data.total;
    setText("workbench-page", t("{0}–{1} of {2} records", all.length ? state.workbenchOffset + 1 : 0, state.workbenchOffset + all.length, displayValue(total)));
    byId("workbench-previous").disabled = state.workbenchOffset === 0;
    byId("workbench-next").disabled = typeof total !== "number" || state.workbenchOffset + DETAIL_PAGE_SIZE >= total;
  }

  function setDrawerMode(mode) {
    state.drawerMode = mode;
    byId("drawer-evidence-content").hidden = mode !== "evidence";
    byId("research-detail").hidden = mode !== "detail";
    byId("drawer-evidence-tab").setAttribute("aria-pressed", String(mode === "evidence"));
    byId("drawer-detail-tab").setAttribute("aria-pressed", String(mode === "detail"));
    setText("drawer-title", mode === "evidence" ? t("Attempts & evidence") : t("Research detail"));
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
    setText("research-detail-status", t("Reading run-bound evidence…"));
    try {
      let data = await fetchDetail({kind, ...parameters, limit: DETAIL_PAGE_SIZE});
      if (request !== state.detailRequest) return;
      if (kind === "resources") {
        data = list(data.experiments)[0];
        if (!data) throw new Error(t("Resource evidence is unavailable for this experiment."));
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
    if (!selected) { content.replaceChildren(element("p", "empty-copy", t("Select a skill, paper, session Attempt, interaction or experiment."))); return; }
    const data = object(selected.data);
    const kind = selected.kind;
    setText("research-detail-title", data.name || object(data.attempt).attempt_id || data.title || data.experiment_id || data.interaction_id || kind);
    const evidenceStatus = data.available === false ? kind === "attempt" && data.attempt?.failure_code
      ? t("Recorded failure · session trace unavailable") : t("Evidence unavailable") : t("Read-only evidence");
    setText("research-detail-status", selected.data ? `${evidenceStatus}${data.scope ? ` · ${data.scope}` : ""}${data.truncated ? t(" · bounded response; more evidence exists") : ""}` : t("Reading run-bound evidence…"));
    const names = kind === "skill" ? [["overview", "Version"], ["body", t("Skill content")], ["usage", t("Calls")], ["evolution", t("Evolution history")], ["diff", t("Recent diff")], ["adoption", "Adoption"], ["files", t("Supporting files")]]
      : kind === "attempt" ? [["overview", t("Attempt")], ["timeline", "Session / tools"], ["corrections", "Corrections"], ["execution", t("Execution evidence")]]
        : kind === "interaction" ? [["overview", "Overview"], ["timeline", "Stages / verdict"]]
          : [["overview", kind === "resource" ? "Estimate → admission → measured" : t("Evidence")]];
    const existingFocus = document.activeElement && document.activeElement.dataset.detailTab;
    tabs.replaceChildren();
    names.forEach(([key, label]) => {
      const button = actionButton(label, () => { state.detailTab = key; renderResearchDetail(); });
      button.dataset.detailTab = key;
      button.setAttribute("aria-pressed", String(state.detailTab === key));
      tabs.append(button);
      if (existingFocus === key) button.focus({preventScroll: true});
    });
    if (selected.parameters) tabs.append(actionButton(t("Refresh"), () => openRemoteDetail(selected.requestKind || kind, state.detail.parameters, true)));
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
      appendDefinition(grid, t("recorded relation"), data.relation);
      if (data.kind === "pruning") {
        appendDefinition(grid, "meaning", t("Pruning is a control decision; it does not refute the scientific claim."));
        appendDefinition(grid, t("winner claim"), object(data.source).claim || "—");
        appendDefinition(grid, t("pruned claim"), object(data.target).claim || "—");
        appendDefinition(grid, t("pruned scientific assessment"), object(data.target).assessment || t("Not assessed"));
      } else if (data.kind === "lineage") {
        appendHypothesisComparison(grid, data.target, data.source);
        appendDefinition(grid, t("child claim"), object(data.target).claim || "—");
        appendDefinition(grid, t("child method family"), object(data.target).method_family_id || "—");
        appendDefinition(grid, t("child scientific assessment"), object(data.target).assessment || t("Not assessed"));
      }
      content.append(grid);
      const raw = element("details", "raw-evidence");
      raw.append(element("summary", "", t("Raw relationship evidence")));
      structuredDetail(raw, {relationship: data.recorded, source: data.source, target: data.target});
      content.append(raw);
    } else if (state.detailTab === "execution") {
      structuredDetail(content, data.trace_evidence || {coverage: "unavailable", notice: t("No retained execution trace evidence.")});
      list(data.related_attempts || data.attempt_links).forEach((attempt) => {
        const id = typeof attempt === "string" ? attempt : attempt.attempt_id;
        if (id) content.append(actionButton(id, () => openRemoteDetail("attempt", {attempt_id: id}), "detail-link reliability-attempt-link"));
      });
    } else if (kind === "skill" && state.detailTab === "usage") {
      structuredDetail(content, data.usage || {coverage: "unavailable"});
      renderSkillCalls(content, data.calls, data.calls_truncated);
    } else if (kind === "skill" && state.detailTab === "evolution") {
      renderSkillEvolution(content, data.evolutions);
    } else if (state.detailTab === "body") {
      if (data.body_truncated) content.append(element("p", "panel-copy", t("Skill body truncated at the response limit.")));
      content.append(element("pre", "detail-code skill-body", data.removed ? t("This skill was removed in the selected accepted version.") : data.body ?? t("Skill content unavailable for this accepted version.")));
    }
    else if (state.detailTab === "diff") {
      const diff = object(data.diff);
      content.append(element("p", "panel-copy", `${diff.base_commit || t("No accepted base")} → ${diff.commit || data.commit || "unknown"}${diff.truncated ? " · truncated" : ""}`));
      const code = element("pre", "detail-code skill-diff");
      const text = diff.available === false ? t("Diff unavailable.") : diff.text ?? t("No accepted evolution diff.");
      text.split("\n").forEach((line) => code.append(element("span", line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : "", `${line}\n`)));
      content.append(code);
    } else if (state.detailTab === "adoption") {
      content.append(element("p", "panel-copy", t("Published commit → later request binding → native Skill load. These are separate observations; an unavailable transcript cannot prove a load.")));
      const adoption = list(data.adoption);
      if (!adoption.length) content.append(element("p", "empty-copy", t("No later adoption evidence recorded.")));
      adoption.forEach((item) => {
        const row = element("article", "detail-card"); structuredDetail(row, item);
        if (item.attempt_id) row.append(actionButton(t("Inspect native session"), () => openRemoteDetail("attempt", {attempt_id: item.attempt_id})));
        content.append(row);
      });
    } else if (state.detailTab === "files") {
      if (data.supporting_files_truncated) content.append(element("p", "panel-copy", t("Only the first 100 supporting files are shown.")));
      list(data.supporting_files).forEach((file) => {
        const section = element("details");
        section.append(element("summary", "", t("{0} · {1} bytes{2}", file.path, file.size ?? "?", file.truncated ? " · truncated" : "")),
          element("pre", "detail-code", file.text ?? t("Binary or unavailable supporting asset."))); content.append(section);
      });
      if (!list(data.supporting_files).length) content.append(element("p", "empty-copy", t("No supporting files projected.")));
    } else if (state.detailTab === "timeline") {
      const events = kind === "interaction" ? list(data.stages) : list(data.events);
      events.forEach((event, index) => {
        const row = element("article", "timeline-event");
        row.append(element("h4", "", `${(data.offset || 0) + index + 1}. ${event.type || event.action || "event"}${event.name ? ` · ${event.name}` : ""}`));
        structuredDetail(row, event); content.append(row);
      });
      if (!events.length) content.append(element("p", "empty-copy", t("No archived timeline available in this page.")));
      if (kind === "attempt") {
        const offset = data.offset || 0;
        const controls = element("div", "pagination");
        if (offset > 0) controls.append(actionButton(t("Earlier events"), () => openRemoteDetail("attempt", {...state.detail.parameters, offset: Math.max(0, offset - DETAIL_PAGE_SIZE)}, true)));
        if (typeof data.total === "number" && offset + events.length < data.total) controls.append(actionButton(t("Later events"), () => openRemoteDetail("attempt", {...state.detail.parameters, offset: offset + DETAIL_PAGE_SIZE}, true)));
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
        versions.append(element("p", "panel-copy", t("Accepted / bound versions (concurrent bindings may differ):")));
        list(data.versions).forEach((version) => versions.append(actionButton(
          `${compact(version.commit, 18)}${version.current ? " · current/bound" : ""}`,
          () => openRemoteDetail("skill", {role: data.role, name: data.name, commit: version.commit}))));
        content.append(versions);
      }
      if (kind === "attempt") {
        if (data.attempt?.failure_stage === "literature_retrieval") content.append(element("p", "panel-copy", t("Literature preparation failed before model invocation. Missing session/tool traces do not hide the recorded failure diagnostics.")));
        content.append(element("p", "panel-copy", t("Normalized turns and raw provider counters may use different units. OPEN is durable state; current live subphase is unknown here.")));
      }
      if (kind === "literature") {
        paperSourceLink(content, t("Open paper ↗"), data.landing_uri || data.url || data.doi_url, data);
        paperSourceLink(content, t("Open PDF / retrieval source ↗"), data.source_uri || data.pdf_url, data);
        renderDownloadEvidence(content, data);
      }
      list(data.source_attempt_ids).forEach((attempt_id) => content.append(actionButton(t("Source Attempt {0}", attempt_id), () => openRemoteDetail("attempt", {attempt_id}))));
      if (data.update_attempt && data.update_attempt.attempt_id) content.append(actionButton(t("Inspect evolution session"), () => openRemoteDetail("attempt", {attempt_id: data.update_attempt.attempt_id})));
    }
    content.scrollTop = top;
    const nextText = content.querySelector(".skill-body, .skill-diff");
    if (nextText) nextText.scrollTop = textScroll;
  }

  function renderProduct(product) {
    setText("product-status", t("Observed {0} · {1}", product.run_id, formatTimestamp(product.observed_at)));
    const experimentPage = state.history.experiments || product;
    const eventPage = state.history.events || product;
    setText("experiment-total", object(object(object(product.summary).totals).experiments).total ?? "unavailable");
    renderChanged("experiment-queue", [experimentPage.experiments, experimentPage.experiments_page], () => {
      const queue = byId("experiment-queue"); queue.replaceChildren();
      list(experimentPage.experiments).slice().reverse().forEach((experiment) => {
        const button = element("button", "experiment-row");
        button.type = "button";
        button.append(element("strong", "", experiment.experiment_id), element("span", "counter", experiment.state),
          element("span", "panel-copy", object(experiment.outcome).outcome || t("No accepted outcome")));
        button.addEventListener("click", () => openExperimentLog(experiment.experiment_id));
        queue.append(button);
      });
      if (!queue.children.length) queue.append(element("p", "empty-copy", t("No experiments submitted yet.")));
      renderHistoryControls("experiments", experimentPage);
    });
    const report = object(product.final_report);
    renderChanged("report", report, () => {
      setText("final-report-source", report.attempt_id ? t("As-of report from {0} · {1}. {2} Later stop controls remain in the event history.", report.attempt_id, report.verdict || "NO_ACCEPTED_VERDICT", report.stop_reason) : t("No accepted report yet. A report is a narrative, not a new scientific verdict."));
      setText("final-report-text", report.markdown || t("No accepted report yet."));
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
    setText(`${kind}-page`, t("{0} · {1} of {2} · offset {3}", page.offset ? t("Retained older page") : t("Latest page"), count, page.total ?? count, page.offset || 0));
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
      if (!response.ok) throw new Error(t("History page unavailable"));
      const data = await response.json();
      if (request !== state.historyRequests[kind]) return;
      state.history[kind] = data;
      if (state.activeView === "activity") renderProduct(state.product);
    } catch (_error) {
      if (request === state.historyRequests[kind]) {
        setText(`${kind}-page`, t("History page unavailable; retained rows remain visible."));
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
      if (Number.isFinite(state.snapshot?.observed_at)) headers["X-Hypoloom-Observed-At"] = String(state.snapshot.observed_at);
      const response = await window.fetch("/api/refresh", {
        method: "GET",
        cache: "no-store",
        credentials: "same-origin",
        headers,
        signal: controller.signal,
      });
      if (response.status === 401) {
        state.pollError = accessToken ? "TOKEN REJECTED" : "TOKEN REQUIRED";
        setConnection("error", accessToken ? t("TOKEN REJECTED") : t("TOKEN REQUIRED"));
        announce(t("Snapshot access was not authorized. Open the startup URL containing its token fragment."));
        return;
      }
      if (!response.ok) throw new Error(t("snapshot endpoint returned {0}", response.status));
      const refresh = await response.json();
      if (response.status === 202 && refresh.observation?.state === "pending") {
        state.pollFailures = 0;
        state.pollError = null;
        // A restarted observer has not yet supplied a replacement for retained
        // browser data. Keep its original age; a pending reply is not freshness.
        if (!state.snapshot) {
          state.lastSuccessAt = Date.now();
          state.observation = refresh.observation;
        } else if (state.observation) {
          state.observation = {...state.observation, state: "ready", refreshing: true};
        }
        updateConnection(true);
        return;
      }
      const snapshot = refresh.snapshot;
      if (refresh.unchanged === true && !Number.isFinite(refresh.observation?.age_seconds)) {
        throw new Error(t("snapshot response has an invalid shape"));
      }
      if (!(refresh.unchanged === true && state.snapshot) &&
          (!snapshot || typeof snapshot !== "object" || typeof snapshot.run_id !== "string")) {
        throw new Error(t("snapshot response has an invalid shape"));
      }
      state.lastSuccessAt = Date.now();
      state.pollFailures = 0;
      state.pollError = null;
      state.observation = refresh.observation || null;
      if (document.hidden) return;
      if (refresh.unchanged === true) {
        if (refresh.oracle) state.snapshot = {...state.snapshot, oracle: refresh.oracle};
        renderHud(state.snapshot);
        renderVisibleViews();
      } else {
        render(snapshot, refresh.activity);
      }
      updateConnection(true);
      if (state.activeView === "workbench" && state.workbenchKind !== "reliability") {
        const current = state.snapshot;
        const input = JSON.stringify([current.status?.run_revision, current.fact_counts, current.overview?.totals, workbenchQuery()]);
        if (state.renderInputs.get("workbench-fetch") !== input) {
          state.renderInputs.set("workbench-fetch", input);
          // Selected details own their timeout and request identity; they must
          // not delay the next status poll.
          void loadWorkbench();
        }
      }
      if (document.hidden) return;
      announce(t("Observed run {0} at revision {1}", state.snapshot.run_id, object(state.snapshot.status).run_revision ?? "unknown"));
    } catch (error) {
      if (document.hidden) return;
      state.pollFailures += 1;
      state.pollError = error && error.name === "AbortError" ? "POLL TIMEOUT" : "RECONNECTING";
      updateConnection(true);
      announce(t("Snapshot poll failed; the last successful read remains visible and may be stale."));
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
    announce(t("Pruning links {0}; hypothesis nodes and their statuses remain visible", state.showPruning ? "shown" : "hidden"));
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
    state.skillFilters = {window: "", command: ""};
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
    setText("human-title", t("Human decision · question {0}", decision.ordinal));
    setText("human-question", decision.question);
    setText("human-rationale", decision.rationale);
    setText("human-access", answerToken ? t("Operator answer access. Review and submit to continue this run.")
      : t("This link is read-only. In TUI press h to answer directly or choose Browser for an operator link. For standalone Web, launch with --allow-human-answers."));
    byId("human-answer").value = humanDraft().text;
    byId("human-options").replaceChildren();
    list(decision.options).forEach((option, index) => {
      const card = element("article", "");
      const button = element("button", "", t("Use option {0}", index + 1));
      button.type = "button";
      button.addEventListener("click", () => {
        byId("human-answer").value = option; humanDraft().text = option; humanButtons();
        byId("human-answer").focus(); byId("human-answer").scrollIntoView({block: "center"});
      });
      card.append(element("p", "", option), button); byId("human-options").append(card);
    });
    byId("submit-human").textContent = humanDraft().pending ? t("Retry same answer") : t("Submit answer");
    setText("human-status", humanDraft().pending ? t("Previous delivery was not confirmed. Retry preserves the exact answer.")
      : t("Choose a suggestion or write your own answer, then review. Nothing is sent until you submit."));
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
      const option = element("option", "", t("Question {0} · {1}", item.ordinal, item.state === "OPEN" ? t("Pending") : item.resolution || t("Answered")));
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
      option.textContent = t("Question {0} · {1}", item.ordinal, item.state === "OPEN" ? t("Pending") : item.resolution || t("Answered"));
    }
    byId("human-pending").hidden = !pending.length;
    if (pending.length) {
      setText("human-pending-title", t("{0} decision pending · waiting for you", pending.length));
      setText("human-pending-question", compact(pending[0].question, 240));
    }
    if (humanCurrent) {
      const selected = decisions.find(item => item.decision_id === humanCurrent.decision_id);
      const resolved = !selected || selected.state !== "OPEN";
      byId("human-recorded").hidden = !resolved;
      if (resolved) {
        setText("human-recorded", t("Recorded answer: {0}", selected?.answer || selected?.resolution || "unavailable"));
        humanDraft().pending = null;
        setText("human-status", t("This decision is no longer pending. View its recorded answer or select another question."));
      } else if (humanCurrent.revision >= 0 && status.run_revision !== humanCurrent.revision && !humanDraft().pending) {
        setText("human-status", t("The run changed. Close and reopen to review the current decision; your draft is retained."));
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
    setText("human-status", t("Review the answer above. Submit answer records it and continues this run; Edit returns to the draft."));
  });
  byId("edit-human").addEventListener("click", () => { humanReviewed = false; humanButtons(); });
  byId("submit-human").addEventListener("click", async () => {
    if (!humanWritable() || !humanReviewed || humanBusy) return;
    const draft = humanDraft();
    if (!draft.pending) draft.pending = {decision_id: humanCurrent.decision_id, answer: draft.text,
      expected_revision: humanCurrent.revision, request_id: `human-ui-${window.crypto.randomUUID()}`};
    humanBusy = true; humanButtons(); setText("human-status", t("Submitting to Host…"));
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
        setText("human-status", t("Answer recorded. The Host can continue this run."));
      } else if ([400, 401, 403, 409].includes(response.status)) {
        draft.pending = null;
        humanCurrent.revision = -1;
        setText("human-status", t("Answer not accepted: {0}. Refresh the observer or reopen an operator link before reviewing again.", object(reply.error).message || reply.message || reply.error || response.status));
      } else {
        setText("human-status", t("Host reply not confirmed. Retry the same answer or inspect recorded state."));
      }
      poll();
    } catch (_error) {
      setText("human-status", t("Host reply not confirmed. Retry sends the same answer safely; nothing is automatically resent."));
    } finally {
      window.clearTimeout(timer); humanBusy = false;
      byId("submit-human").textContent = draft.pending ? t("Retry same answer") : t("Submit answer");
      humanButtons();
    }
  });

  const trendsDialog = byId("trends-dialog");
  let trendsData = null, trendsController = null, trendsTimer = null;
  let trendIndices = [], trendSeries = [], hiddenTrendSeries = new Set();
  const trendColors = ["#78dcff", "#e5b66f", "#b69cff", "#73e0b1", "#ff97bd", "#b3ce70", "#8aafff", "#efa07e"];
  const trendTime = at => new Date(at * 1000).toLocaleString(uiLanguage, {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"});
  const trendNumber = value => value == null ? "—" : Number(value).toLocaleString(uiLanguage, {maximumFractionDigits:2});
  function trendSvg(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }
  function renderTrendChart(id, series) {
    const root = byId(id); root.replaceChildren();
    const width = Math.max(280, Math.round(root.getBoundingClientRect().width || 900)), height = 220, left = 40, right = 15, top = 15, bottom = 35;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const values = series.flatMap(s => trendIndices.map(i => s.values[i])).filter(v => Number.isFinite(v));
    const ceiling = Math.max(1, Math.ceil(values.reduce((highest, value) => Math.max(highest, value), 0)));
    const svg = trendSvg("svg", {viewBox:`0 0 ${width} ${height}`, role:"img", "aria-label":id === "trends-rpm" ? t("Requests per minute") : t("Experiment and Agent concurrency")});
    const x = j => left + (j / Math.max(1, trendIndices.length - 1)) * plotWidth;
    const y = value => top + plotHeight * (1 - value / ceiling);
    for (let i = 0; i <= 4; i++) {
      const value = ceiling * i / 4;
      svg.append(trendSvg("line", {x1:left, x2:width-right, y1:y(value), y2:y(value), class:"trend-grid"}));
      const label = trendSvg("text", {x:left-8, y:y(value)+4, "text-anchor":"end"}); label.textContent = trendNumber(value); svg.append(label);
    }
    if (trendIndices.length) {
      const ticks = width < 500 ? [0, trendIndices.length-1] : [0, Math.floor((trendIndices.length-1)/2), trendIndices.length-1];
      ticks.forEach((j, k) => {
        const label = trendSvg("text", {x:x(j), y:height-9, "text-anchor":k === 0 ? "start" : k === ticks.length-1 ? "end" : "middle"});
        label.textContent = trendTime(trendsData.points[trendIndices[j]].until); svg.append(label);
      });
    }
    series.forEach(s => {
      let path = "", connected = false;
      trendIndices.forEach((i, j) => {
        const value = s.values[i];
        if (!Number.isFinite(value)) { connected = false; return; }
        if (!connected) svg.append(trendSvg("circle", {cx:x(j), cy:y(value), r:2, fill:s.color}));
        path += `${connected ? "L" : "M"}${x(j)},${y(value)} `; connected = true;
      });
      svg.append(trendSvg("path", {d:path, fill:"none", stroke:s.color, "stroke-width":s.dashed ? 1.4 : 2.4,
        "stroke-dasharray":s.dashed ? "5 5" : "none", "vector-effect":"non-scaling-stroke"}));
    });
    svg.append(trendSvg("line", {class:"trend-crosshair", x1:left, x2:left, y1:top, y2:height-bottom}));
    svg.addEventListener("pointermove", event => {
      const rect = svg.getBoundingClientRect();
      const j = Math.round((((event.clientX-rect.left) / rect.width * width)-left) / plotWidth * (trendIndices.length-1));
      byId("trends-cursor").value = String(Math.max(0, Math.min(trendIndices.length-1, j)));
      inspectTrendPoint();
    });
    root.append(svg);
  }
  function inspectTrendPoint() {
    if (!trendIndices.length) return;
    const j = Number(byId("trends-cursor").value), index = trendIndices[j], point = trendsData.points[index];
    document.querySelectorAll(".trend-crosshair").forEach(line => {
      const x = 40 + j / Math.max(1, trendIndices.length-1) * (line.ownerSVGElement.viewBox.baseVal.width - 55);
      line.setAttribute("x1", x); line.setAttribute("x2", x);
    });
    const content = byId("trends-inspect"); content.replaceChildren();
    content.append(element("strong", "", `${trendTime(point.at)} – ${trendTime(point.until)}`));
    content.append(element("p", "", t("Experiments: {0} (peak {1}) · Agents: {2} (peak {3})", point.experiments, point.experiments_peak, point.agents, point.agents_peak)));
    const metrics = element("div", "trend-values");
    trendSeries.forEach(s => {
      if (hiddenTrendSeries.has(s.key)) return;
      const value = s.values[index];
      const label = value == null ? t("unknown; recorded ≥{0} RPM", trendNumber(s.lower[index])) : `${trendNumber(value)} RPM`;
      const row = element("span", "", `${s.label}: ${label}`); row.style.color = s.color; metrics.append(row);
    });
    content.append(metrics);
  }
  function renderTrends() {
    if (!trendsData) return;
    const range = byId("trends-range").value;
    const since = range === "all" ? -Infinity : trendsData.ended_at - Number(range);
    trendIndices = trendsData.points.map((_, i) => i).filter(i => trendsData.points[i].until >= since);
    byId("trends-content").hidden = !trendIndices.length;
    const coverage = object(trendsData.coverage);
    byId("trends-coverage").textContent = t("{0} s buckets · {1} recorded requests · {2} Attempts with incomplete capture. Gaps are unknown; known lower bounds remain available below. Data is included in Report issue.",
      trendsData.interval_seconds, coverage.request_count ?? 0, coverage.incomplete_attempts ?? 0);
    const concurrency = [
      {key:"experiments", label:t("Experiments"), color:trendColors[0]},
      {key:"agents", label:"Agents", color:trendColors[1]},
    ];
    const legend = byId("trends-concurrency-legend"); legend.replaceChildren();
    concurrency.forEach(s => { const label = element("span", "", s.label); label.style.color=s.color; legend.append(label); });
    renderTrendChart("trends-concurrency", concurrency.flatMap(s => [
      {...s, values:trendsData.points.map(p => p[s.key])},
      {...s, dashed:true, values:trendsData.points.map(p => p[s.key+"_peak"])},
    ]));
    trendSeries = [{key:"run", label:t("Total RPM"), color:trendColors[0],
      values:trendsData.points.map(p => p.rpm), lower:trendsData.points.map(p => p.rpm_lower_bound)},
      ...trendsData.experts.map((s, i) => ({key:`expert:${s.role}`, label:s.role, color:trendColors[(i+1)%trendColors.length], values:s.rpm, lower:s.rpm_lower_bound})),
      ...["grounding", "other"].map((key, i) => ({key, label:key === "grounding" ? "Grounding" : t("Other run tasks"), color:["#8cabbc", "#df9b9b"][i], values:trendsData[key].rpm, lower:trendsData[key].rpm_lower_bound}))];
    const rpmLegend = byId("trends-rpm-legend"); rpmLegend.replaceChildren();
    trendSeries.forEach(s => {
      const button = element("button", "", s.label); button.type="button"; button.style.color=s.color;
      button.setAttribute("aria-pressed", String(!hiddenTrendSeries.has(s.key)));
      button.addEventListener("click", () => {
        if (hiddenTrendSeries.has(s.key)) hiddenTrendSeries.delete(s.key); else hiddenTrendSeries.add(s.key);
        renderTrends();
      }); rpmLegend.append(button);
    });
    renderTrendChart("trends-rpm", trendSeries.filter(s => !hiddenTrendSeries.has(s.key)));
    byId("trends-cursor").max = String(Math.max(0, trendIndices.length-1));
    inspectTrendPoint();
  }
  async function loadTrends() {
    if (!trendsDialog.open || trendsController) return;
    clearTimeout(trendsTimer);
    const controller = new AbortController(); trendsController = controller;
    const timeout = setTimeout(() => controller.abort(), 30000);
    byId("refresh-trends").disabled = true;
    byId("trends-status").textContent = t("Reading recorded runtime trends…");
    try {
      const response = await fetch("/api/trends", {headers:{Authorization:`Bearer ${accessToken}`}, signal:controller.signal, cache:"no-store"});
      if (!response.ok) throw new Error("unavailable");
      const data = await response.json();
      if (!trendsDialog.open || controller.signal.aborted) return;
      trendsData = data;
      renderTrends();
      byId("trends-cursor").value = byId("trends-cursor").max;
      inspectTrendPoint();
      byId("trends-status").textContent = t("Observed {0}. Refreshes every minute while open.", trendTime(data.observed_at));
    } catch (_error) {
      if (trendsDialog.open) byId("trends-status").textContent = t("Trends unavailable. Any previous chart is retained; use Refresh trends to retry.");
    } finally {
      clearTimeout(timeout); trendsController = null;
      byId("refresh-trends").disabled = false;
      if (trendsDialog.open) trendsTimer = setTimeout(loadTrends, 60000);
    }
  }
  byId("open-trends").addEventListener("click", () => { trendsDialog.showModal(); loadTrends(); });
  byId("close-trends").addEventListener("click", () => trendsDialog.close());
  trendsDialog.addEventListener("close", () => { clearTimeout(trendsTimer); trendsController?.abort(); });
  byId("refresh-trends").addEventListener("click", loadTrends);
  byId("trends-range").addEventListener("change", () => { renderTrends(); byId("trends-cursor").value=byId("trends-cursor").max; inspectTrendPoint(); });
  byId("trends-cursor").addEventListener("input", inspectTrendPoint);
  let trendResizeFrame = null;
  window.addEventListener("resize", () => {
    if (!trendsDialog.open || trendResizeFrame) return;
    trendResizeFrame = requestAnimationFrame(() => { trendResizeFrame = null; renderTrends(); });
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
    setText("support-status", t("Selection changed. Preview this package before downloading."));
  }
  function openSupport() {
    const scope = byId("support-scope");
    Array.from(scope.querySelectorAll("[data-attempt-option]")).forEach(option => option.remove());
    list(state.snapshot?.attempts).slice(-100).reverse().forEach(attempt => {
      const option = element("option", "", `${attempt.role || attempt.kind || t("Attempt")} · ${attempt.command || attempt.phase || attempt.attempt_id} · ${attempt.terminal_reason || attempt.state || "unknown"}`);
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
    setText("support-status", format === "zip" ? t("Preparing download…") : t("Reading available diagnostic evidence…"));
    try {
      const response = await window.fetch(`/api/support?${supportOptions(format)}`, {
        method: "POST", headers: {Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json"},
        body: JSON.stringify(Object.fromEntries(["actual", "expected", "steps", "occurred_at"]
          .map(key => [key, byId(`support-${key}`).value]).filter(([, value]) => value))),
        cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error(response.status === 401 ? t("Open the authenticated observer URL first.")
        : response.status === 429 ? t("Another package is being prepared. Try again shortly.") : t("Could not prepare this selection. Try a different scope or use the CLI."));
      if (format === "zip") {
        const blob = await response.blob();
        if (request !== supportRequest) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = byId("support-private").checked ? "hypoloom-support-private.zip" : "hypoloom-support.zip";
        document.body.append(link); link.click(); link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        setText("support-status", t("Download requested. Inspect README.md and the files locally before sharing; nothing was uploaded."));
      } else {
        const preview = await response.json();
        if (request !== supportRequest) return;
        setText("support-readme", preview.readme);
        setText("support-report", JSON.stringify(preview.report, null, 2));
        setText("support-diagnostics", JSON.stringify(preview.diagnostics, null, 2));
        byId("support-preview").hidden = false;
        const report = object(preview.report);
        const selection = object(report.selection);
        const coverage = object(report.coverage);
        const counts = object(coverage.attachment_status_counts);
        setText("support-status", t("{0} selected + {1} supporting details / {2} recorded attempts. {3} files · {4} bytes before report metadata. {5}{6}{7}{8} Download reads current evidence with these options.", selection.selected_detail_count ?? 0, selection.supporting_detail_count ?? 0, selection.run_attempt_count ?? "?", report.exported_file_count ?? "?", compactNumber(report.exported_payload_bytes), report.limited ? t("Limited: {0}. ", list(coverage.limiting_reasons).join(", ") || t("see inventory")) : "", counts.not_inspected ? t("{0} scopes/files not inspected; their contents are unknown. ", counts.not_inspected) : "", object(report.diagnosis).available ? "" : t("Run diagnosis unavailable; available fallback evidence is included. "), list(coverage.next_steps).join(" ")));
      }
      byId("download-support").disabled = false;
    } catch (error) {
      if (request !== supportRequest) return;
      setText("support-status", error?.name === "AbortError" ? t("Preparation timed out. Try a smaller scope or use the CLI.") : error.message);
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

  function applyLanguage(language) {
    const connectionState = connection.dataset.state;
    const connectionMessage = connectionText.textContent;
    uiLanguage = language === "zh-CN" ? "zh-CN" : "en";
    document.documentElement.lang = uiLanguage;
    const localize = source => uiLanguage === "en" ? source : t(source.trim().replace(/\s+/g, " "));
    staticMessages.forEach(entry => {
      const {node, source, rendered} = entry;
      // A snapshot, log stream or operator response may have replaced an initial
      // placeholder. Never restore that placeholder over the current content.
      if (node.isConnected && node.hasAttribute("data-i18n") && node.textContent === rendered) {
        node.textContent = entry.rendered = localize(source);
      }
    });
    staticAttributes.forEach(({node, name, source}) => node.setAttribute(name, localize(source)));
    const button = byId("language-toggle");
    button.textContent = uiLanguage === "en" ? "中文" : "English";
    button.lang = uiLanguage === "en" ? "zh-CN" : "en";
    button.title = t(uiLanguage === "en" ? "Switch to Chinese" : "Switch to English");
    state.renderInputs.clear();
    if (state.snapshot) render(state.snapshot, state.product);
    if (trendsDialog.open) renderTrends();
    if (!drawer.hidden && state.drawerMode === "detail") renderResearchDetail();
    if (document.hidden) setConnection(connectionState, connectionMessage);
    else updateConnection(true);
    // Keep operator drafts, reviewed answers, pending request IDs and current
    // tab/filter selections. Language selection never sends an operation.
    humanButtons();
  }
  const languageButton = byId("language-toggle");
  languageButton.addEventListener("click", () => {
    applyLanguage(uiLanguage === "en" ? "zh-CN" : "en");
    try { window.localStorage.setItem(LANGUAGE_STORAGE_KEY, uiLanguage); } catch (_) { /* Session-only when storage is unavailable. */ }
  });
  // Loading translations must not delay or disable ordinary English observation.
  window.fetch("/assets/messages.json", {cache: "no-store"}).then(response => {
    if (!response.ok) throw new Error("translation unavailable");
    return response.json();
  }).then(catalog => {
    if (!catalog || typeof catalog !== "object" || Array.isArray(catalog)) throw new Error("translation unavailable");
    messages = catalog;
    let savedLanguage;
    try { savedLanguage = window.localStorage.getItem(LANGUAGE_STORAGE_KEY); } catch (_) { /* Default English. */ }
    applyLanguage(savedLanguage);
    languageButton.disabled = false;
  }).catch(() => { languageButton.title = "Translation unavailable; English remains available."; });

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
    setText("screen-status", t("Text size set to {0}.", label));
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
      setConnection("stale", t("PAUSED · TAB HIDDEN"));
    } else {
      renderVisibleViews();
      if (state.activeView === "activity" && state.selectedExperiment) openExperimentLog(state.selectedExperiment);
      poll();
    }
  });

  window.setInterval(updateConnection, 1000);

  if (!accessToken) {
    state.pollError = "TOKEN REQUIRED";
    setConnection("error", t("TOKEN REQUIRED"));
  }
  setActiveView("operations");
  poll();
})();

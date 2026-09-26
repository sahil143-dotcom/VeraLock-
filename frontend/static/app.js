(() => {
  const ACTIVE = new Set(["DETECTED", "AWAITING_CLARIFICATION", "CONFIRMED"]);
  const STORE = "veralock-demo-v1";

  const SAMPLES = [
    {
      text: "I will send the proposal by Friday.",
      aside: "a clear promise",
    },
    {
      text: "Sounds good.",
      aside: "only an acknowledgement",
    },
    {
      text: "I'll handle the budget review soon.",
      aside: "not yet specific",
    },
    {
      text: "Maybe the budget review sometime, I'm not sure.",
      aside: "still unclear, same sitting",
    },
    {
      text: "I might send the proposal by Friday.",
      aside: "an intention, not a promise",
    },
    {
      text: "um",
      aside: "nothing to weigh",
    },
  ];

  const els = {
    form: document.querySelector("#turn-form"),
    session: document.querySelector("#session-id"),
    speaker: document.querySelector("#speaker-role"),
    intervened: document.querySelector("#intervened"),
    text: document.querySelector("#text"),
    send: document.querySelector("#send"),
    error: document.querySelector("#error"),
    chips: document.querySelector("#chips"),
    hero: document.querySelector("#hero-body"),
    transcript: document.querySelector("#transcript"),
    evidence: document.querySelector("#evidence"),
    vault: document.querySelector("#vault-meta"),
    health: document.querySelector("#health"),
    foot: document.querySelector("#foot"),
    newSession: document.querySelector("#new-session"),
  };

  const state = {
    sessionId: "",
    history: [],
    active: {},
    sending: false,
  };

  function id(prefix) {
    const rand = Math.random().toString(36).slice(2, 8);
    return `${prefix}-${Date.now().toString(36)}-${rand}`;
  }

  function loadStore() {
    try {
      return JSON.parse(localStorage.getItem(STORE) || "{}");
    } catch (_err) {
      return {};
    }
  }

  function save() {
    const all = loadStore();
    all.current = state.sessionId;
    all.sessions = all.sessions || {};
    all.sessions[state.sessionId] = {
      history: state.history,
      active: state.active,
      speakerRole: els.speaker.value,
    };
    localStorage.setItem(STORE, JSON.stringify(all));
  }

  function adoptSession(sessionId, { reset } = { reset: false }) {
    const all = loadStore();
    const saved = !reset && all.sessions ? all.sessions[sessionId] : null;
    state.sessionId = sessionId;
    state.history = saved ? saved.history || [] : [];
    state.active = saved ? saved.active || {} : {};
    els.session.value = sessionId;
    if (saved && saved.speakerRole) els.speaker.value = saved.speakerRole;
    save();
    render();
    refreshVault();
  }

  function rememberCommitment(commitment) {
    if (!commitment || !commitment.commitment_id) return;
    if (ACTIVE.has(commitment.status)) {
      state.active[commitment.commitment_id] = commitment;
    } else {
      delete state.active[commitment.commitment_id];
    }
  }

  function activeCommitments() {
    return Object.values(state.active);
  }

  function setError(message) {
    if (!message) {
      els.error.hidden = true;
      els.error.textContent = "";
      return;
    }
    els.error.hidden = false;
    els.error.textContent = message;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function yesNo(value) {
    return value ? "yes" : "no";
  }

  function annotation(commitment) {
    const parts = [
      `topic ${commitment.topic_id || "—"}`,
      `acknowledgement ${yesNo(commitment.is_acknowledgement)}`,
      `intention only ${yesNo(commitment.is_intention_only)}`,
    ];
    return parts.join("  ·  ");
  }

  function renderLedger(commitment) {
    const block = el("div", "ledger");
    block.append(
      el("p", "ledger-status", commitment.status || "—"),
      el("p", "canonical", commitment.canonical_text || ""),
      el("p", "annotation", annotation(commitment)),
    );
    const idLine = el("p", "annotation mono", commitment.commitment_id || "");
    block.append(idLine);
    return block;
  }

  function renderSpeech(result) {
    if (result.speech_action === "CLARIFY" && result.clarification_question) {
      const lean = el("div", "lean-in");
      const kicker = el("p", "ask", "VeraLock leans in");
      const question = el("p", "question", result.clarification_question);
      lean.append(kicker, question);
      lean.setAttribute("aria-label", "Speech action CLARIFY");
      return lean;
    }
    const breath = el("p", "breath");
    breath.setAttribute("aria-label", `Speech action ${result.speech_action || "SILENT"}`);
    if (result.skipped_by_filter) {
      breath.textContent = `set aside · ${result.filter_reason || "skip"}`;
    } else if (result.speech_action === "SILENT") {
      breath.textContent = "silent";
    } else {
      breath.textContent = (result.speech_action || "silent").toLowerCase();
    }
    return breath;
  }

  function renderDebug(entry) {
    const details = document.createElement("details");
    const result = entry.result || {};
    if (result.skipped_by_filter) details.open = true;
    const summary = el("summary", null, "Filter and policy notes");
    const notes = (result.policy_notes || []).join(", ") || "none";
    const line = el(
      "p",
      "annotation",
      result.skipped_by_filter
        ? `Filter skipped this turn (${result.filter_reason || "skip"}). Policy notes: ${notes}.`
        : `Filter: ${result.filter_reason || "pass"}. Policy notes: ${notes}.`,
    );
    const pre = el(
      "pre",
      null,
      JSON.stringify(
        {
          speech_action: result.speech_action || null,
          clarification_question: result.clarification_question || null,
          skipped_by_filter: Boolean(result.skipped_by_filter),
          filter_reason: result.filter_reason || null,
          policy_notes: result.policy_notes || [],
        },
        null,
        2,
      ),
    );
    details.append(summary, line, pre);
    return details;
  }

  function renderHero() {
    const entry = state.history[state.history.length - 1];
    els.hero.replaceChildren();
    els.hero.classList.toggle("quiet-note", !entry);
    if (!entry) {
      els.hero.textContent = "Nothing has been said. VeraLock is still.";
      return;
    }
    const sheet = el("div", "settle");
    const result = entry.result;
    sheet.append(renderSpeech(result));
    if (result.commitment) {
      sheet.append(renderLedger(result.commitment));
    } else {
      sheet.append(el("p", "quiet-note", "Nothing was kept from this turn."));
    }
    sheet.append(renderDebug(entry));
    els.hero.append(sheet);
  }

  function renderTranscript() {
    els.transcript.replaceChildren();
    state.history.forEach((entry) => {
      const item = el("li", "turn");
      const result = entry.result;
      item.append(
        el("p", "who", entry.speaker_role),
        el("p", "line", entry.text),
      );
      const after = el("p", "after");
      if (result.skipped_by_filter) {
        after.textContent = `silent · set aside (${result.filter_reason || "skip"})`;
      } else if (result.commitment && result.commitment.status) {
        after.textContent = `${(result.speech_action || "").toLowerCase()} · ${result.commitment.status}`;
      } else {
        after.textContent = (result.speech_action || "").toLowerCase();
      }
      item.append(after);
      if (result.clarification_question) {
        item.append(el("p", "echo", result.clarification_question));
      }
      els.transcript.append(item);
    });
  }

  function renderEvidence(evidence) {
    els.evidence.replaceChildren();
    els.evidence.className = "evidence";
    if (!evidence) {
      els.evidence.classList.add("quiet-note");
      els.evidence.textContent = "A kept promise will show its trail here.";
      return;
    }
    if (evidence.pending) {
      els.evidence.classList.add("quiet-note");
      els.evidence.textContent = "Looking for what was kept…";
      return;
    }
    if (!evidence.available) {
      els.evidence.classList.add("quiet-note");
      els.evidence.textContent = evidence.detail || "No Vault row for this commitment yet.";
      return;
    }
    const commitment = evidence.commitment || {};
    const sheet = el("div", "settle");
    sheet.append(
      el("p", "ledger-status", commitment.status || ""),
      el("p", "kept-line", commitment.canonical_text || ""),
    );
    const events = evidence.events || [];
    if (events.length) {
      events.forEach((event) => {
        const from = event.from_status || "—";
        const note = event.note ? ` · ${event.note}` : "";
        sheet.append(el("p", "event", `${from} → ${event.to_status}${note}`));
      });
    }
    (evidence.source_turns || []).forEach((turn) => {
      sheet.append(el("p", "source-turn", `${turn.speaker_role}: ${turn.content}`));
    });
    els.evidence.append(sheet);
  }

  function render() {
    renderHero();
    renderTranscript();
    const last = state.history[state.history.length - 1];
    renderEvidence(last ? last.evidence : null);
    els.send.disabled = state.sending;
    els.send.textContent = state.sending ? "Sending…" : "Send";
    document.querySelectorAll(".utterance").forEach((button) => {
      button.disabled = state.sending;
    });
  }

  async function refreshVault() {
    const sessionId = state.sessionId;
    if (!sessionId) return;
    try {
      const response = await fetch(`/v1/sessions/${encodeURIComponent(sessionId)}`);
      if (!response.ok || sessionId !== state.sessionId) return;
      const body = await response.json();
      const turns = body.turn_count || 0;
      const commitments = body.commitment_count || 0;
      els.vault.textContent = body.conversation_id
        ? `${turns} turn${turns === 1 ? "" : "s"} kept · ${commitments} commitment${commitments === 1 ? "" : "s"}`
        : "Nothing written down yet.";
    } catch (_err) {
      if (sessionId === state.sessionId) els.vault.textContent = "The ledger could not be read.";
    }
  }

  function asEvidence(body) {
    if (!body || body.available === false) {
      return {
        available: false,
        detail: (body && body.detail) || "No Vault row for this commitment yet.",
      };
    }
    if (body.commitment) return Object.assign({ available: true }, body);
    return null;
  }

  async function readEvidence(url) {
    const response = await fetch(url);
    const body = await response.json().catch(() => ({}));
    if (response.ok) return { route: true, evidence: asEvidence(body) };
    const detail = typeof body.detail === "string" ? body.detail : "";
    if (response.status === 404 && detail.indexOf("commitment not found") === 0) {
      return { route: true, evidence: { available: false, detail: "No Vault row for this commitment yet." } };
    }
    return { route: false, evidence: null };
  }

  async function loadEvidence(commitmentId) {
    if (!commitmentId) return { available: false, detail: "This turn has no commitment id." };
    const missing = { available: false, detail: "No Vault row for this commitment yet." };
    try {
      const primary = await readEvidence(`/v1/evidence/${encodeURIComponent(commitmentId)}`);
      if (primary.route) return primary.evidence || missing;
      const fallback = await readEvidence(`/v1/demo/evidence/${encodeURIComponent(commitmentId)}`);
      if (fallback.route) return fallback.evidence || missing;
      return { available: false, detail: "The trail could not be read. The turn is still here." };
    } catch (_err) {
      return { available: false, detail: "The trail could not be read. The turn is still here." };
    }
  }

  async function sendTurn(text) {
    const utterance = (text ?? els.text.value).trim();
    const sessionId = els.session.value.trim();
    const speaker = els.speaker.value.trim();
    if (!sessionId || !speaker || !utterance) {
      setError("Session, speaker, and the words themselves are required.");
      return;
    }
    if (sessionId !== state.sessionId) adoptSession(sessionId);
    setError("");
    state.sending = true;
    render();
    const payload = {
      session_id: state.sessionId,
      turn_id: id("turn"),
      speaker_role: speaker,
      text: utterance,
      created_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      intervened: Boolean(els.intervened.checked),
      active_commitments: activeCommitments(),
    };
    if (payload.intervened) {
      payload.intervention_reason = "demo operator blocked auto-confirm";
    }
    try {
      const response = await fetch("/v1/turn", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = body.detail || `Turn failed (${response.status})`;
        setError(typeof detail === "string" ? detail : JSON.stringify(detail));
        return;
      }
      const entry = {
        turn_id: payload.turn_id,
        speaker_role: payload.speaker_role,
        text: utterance,
        created_at: payload.created_at,
        intervened: payload.intervened,
        result: body,
        evidence: body.commitment && body.commitment.commitment_id
          ? { pending: true }
          : { available: false, detail: "This turn has no commitment id." },
      };
      rememberCommitment(body.commitment);
      state.history.push(entry);
      els.text.value = "";
      save();
      render();
      entry.evidence = await loadEvidence(body.commitment && body.commitment.commitment_id);
      save();
      render();
      refreshVault();
    } catch (_err) {
      setError("Could not reach the demo server.");
    } finally {
      state.sending = false;
      render();
    }
  }

  function renderUtterances() {
    SAMPLES.forEach((sample) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "utterance";
      button.append(el("span", "said", `“${sample.text}”`), el("span", "aside", sample.aside));
      button.addEventListener("click", () => {
        els.text.value = sample.text;
        sendTurn(sample.text);
      });
      els.chips.append(button);
    });
  }

  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendTurn();
  });

  els.text.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendTurn();
    }
  });

  els.newSession.addEventListener("click", () => {
    adoptSession(id("sess"), { reset: true });
    els.text.value = "";
    setError("");
  });

  els.session.addEventListener("change", () => {
    const next = els.session.value.trim();
    if (!next || next === state.sessionId) return;
    adoptSession(next);
  });

  els.speaker.addEventListener("change", save);

  renderUtterances();
  const stored = loadStore();
  adoptSession(stored.current || id("sess"));

  fetch("/health")
    .then((response) => response.json())
    .then((body) => {
      els.health.textContent = body.status === "ok" ? "listening" : "not listening";
    })
    .catch(() => {
      els.health.textContent = "not listening";
    });

  fetch("/v1/demo/status")
    .then((response) => response.json())
    .then((body) => {
      els.foot.textContent = body.db_path
        ? `Fixture mode · no API keys · ${body.db_path}`
        : "Fixture mode · no API keys";
    })
    .catch(() => {});
})();

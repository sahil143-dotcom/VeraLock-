(() => {
  const ACTIVE = new Set(["DETECTED", "AWAITING_CLARIFICATION", "CONFIRMED"]);
  const STORE = "veralock-demo-v1";

  const SAMPLES = [
    {
      label: "Clear commitment",
      expect: "CONFIRMED · SILENT",
      text: "I will send the proposal by Friday.",
    },
    {
      label: "Acknowledgement",
      expect: "NO_COMMITMENT · acknowledgement",
      text: "Sounds good.",
    },
    {
      label: "Ambiguous",
      expect: "CLARIFY · AWAITING_CLARIFICATION",
      text: "I'll handle the budget review soon.",
    },
    {
      label: "Ambiguous follow-up",
      expect: "UNRESOLVED_AMBIGUOUS after the ambiguous chip",
      text: "Maybe the budget review sometime, I'm not sure.",
    },
    {
      label: "Intention only",
      expect: "NO_COMMITMENT · intention",
      text: "I might send the proposal by Friday.",
    },
    {
      label: "Filter skip",
      expect: "skipped_by_filter · filler",
      text: "um",
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

  function badge(className, text) {
    const node = document.createElement("span");
    node.className = className;
    node.textContent = text;
    return node;
  }

  function fact(term, value) {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    if (term === "commitment id" || term === "topic") dd.className = "mono";
    wrap.append(dt, dd);
    return wrap;
  }

  function yesNo(value) {
    return value ? "yes" : "no";
  }

  function renderCommitment(commitment) {
    const block = document.createElement("div");
    const status = badge(`status ${commitment.status || ""}`, commitment.status || "NO STATUS");
    const quote = document.createElement("blockquote");
    quote.className = "canonical";
    quote.textContent = commitment.canonical_text || "(empty canonical text)";
    const facts = document.createElement("dl");
    facts.className = "facts";
    facts.append(
      fact("topic", commitment.topic_id || "—"),
      fact("acknowledgement", yesNo(commitment.is_acknowledgement)),
      fact("intention only", yesNo(commitment.is_intention_only)),
      fact("commitment id", commitment.commitment_id || "—"),
    );
    block.append(status, quote, facts);
    return block;
  }

  function renderDebug(entry) {
    const details = document.createElement("details");
    const skipped = Boolean(entry.result && entry.result.skipped_by_filter);
    if (skipped) details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "Debug · filter skip and policy notes";
    const result = entry.result || {};
    const line = document.createElement("p");
    const notes = (result.policy_notes || []).join(", ") || "none";
    line.textContent = result.skipped_by_filter
      ? `Filter skipped this turn (${result.filter_reason || "skip"}). Policy notes: ${notes}.`
      : `Filter: ${result.filter_reason || "pass"}. Policy notes: ${notes}.`;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(
      {
        skipped_by_filter: Boolean(result.skipped_by_filter),
        filter_reason: result.filter_reason || null,
        policy_notes: result.policy_notes || [],
        speech_action: result.speech_action || null,
        clarification_question: result.clarification_question || null,
        evidence_chain: entry.evidence && entry.evidence.chain ? entry.evidence.chain : null,
      },
      null,
      2,
    );
    details.append(summary, line, pre);
    return details;
  }

  function renderHero() {
    const entry = state.history[state.history.length - 1];
    els.hero.replaceChildren();
    els.hero.classList.toggle("empty", !entry);
    if (!entry) {
      els.hero.textContent = "Send a turn to see speech action, the clarification question, and commitment status.";
      return;
    }
    const result = entry.result;
    const row = document.createElement("div");
    row.className = "hero-speech";
    row.append(badge(`speech ${result.speech_action}`, result.speech_action));
    if (result.skipped_by_filter) {
      row.append(badge("speech SKIP", `FILTER ${result.filter_reason || "SKIP"}`));
    }
    const said = document.createElement("p");
    if (result.speech_action === "CLARIFY" && result.clarification_question) {
      said.className = "question";
      said.textContent = result.clarification_question;
    } else if (result.speech_action === "SILENT") {
      said.textContent = "VeraLock stays silent.";
    } else {
      said.textContent = "No clarification question.";
    }
    els.hero.append(row, said);
    if (result.commitment) els.hero.append(renderCommitment(result.commitment));
    else {
      const none = document.createElement("p");
      none.className = "empty";
      none.textContent = "No commitment on this turn.";
      els.hero.append(none);
    }
    els.hero.append(renderDebug(entry));
  }

  function renderTranscript() {
    els.transcript.replaceChildren();
    state.history.forEach((entry) => {
      const item = document.createElement("li");
      item.className = "turn";
      const meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = `${entry.speaker_role} · ${entry.turn_id}`;
      const text = document.createElement("p");
      text.textContent = entry.text;
      const row = document.createElement("div");
      row.className = "hero-speech";
      const result = entry.result;
      row.append(badge(`speech ${result.speech_action}`, result.speech_action));
      if (result.commitment && result.commitment.status) {
        row.append(badge(`status ${result.commitment.status}`, result.commitment.status));
      } else if (result.skipped_by_filter) {
        row.append(badge("speech SKIP", "FILTER SKIP"));
      }
      item.append(meta, text, row);
      if (result.clarification_question) {
        const q = document.createElement("p");
        q.className = "question";
        q.textContent = result.clarification_question;
        item.append(q);
      }
      els.transcript.append(item);
    });
  }

  function renderEvidence(evidence) {
    els.evidence.replaceChildren();
    if (!evidence) {
      els.evidence.className = "evidence empty";
      els.evidence.textContent = "A commitment id from the last turn will load provenance here.";
      return;
    }
    if (evidence.pending) {
      els.evidence.className = "evidence empty";
      els.evidence.textContent = "Loading Vault provenance…";
      return;
    }
    if (!evidence.available) {
      els.evidence.className = "evidence empty";
      els.evidence.textContent = evidence.detail || "No Vault row for this commitment yet.";
      return;
    }
    els.evidence.className = "evidence";
    const commitment = evidence.commitment || {};
    const heading = document.createElement("p");
    heading.append(badge(`status ${commitment.status || ""}`, commitment.status || "STORED"));
    const quote = document.createElement("blockquote");
    quote.className = "canonical";
    quote.textContent = commitment.canonical_text || "";
    els.evidence.append(heading, quote);

    const events = evidence.events || [];
    if (events.length) {
      const label = document.createElement("p");
      label.className = "section-label";
      label.textContent = "Status events";
      els.evidence.append(label);
      events.forEach((event) => {
        const line = document.createElement("p");
        line.className = "event";
        const from = event.from_status || "—";
        const strong = document.createElement("strong");
        strong.textContent = `${from} → ${event.to_status}`;
        line.append(strong);
        if (event.note) line.append(document.createTextNode(` · ${event.note}`));
        els.evidence.append(line);
      });
    }

    const turns = evidence.source_turns || [];
    if (turns.length) {
      const label = document.createElement("p");
      label.className = "section-label";
      label.textContent = "Source turns";
      els.evidence.append(label);
      turns.forEach((turn) => {
        const line = document.createElement("p");
        line.className = "source-turn";
        line.textContent = `${turn.speaker_role}: ${turn.content}`;
        els.evidence.append(line);
      });
    }
  }

  function render() {
    renderHero();
    renderTranscript();
    const last = state.history[state.history.length - 1];
    renderEvidence(last ? last.evidence : null);
    els.send.disabled = state.sending;
    els.send.textContent = state.sending ? "Sending…" : "Send turn";
    document.querySelectorAll(".chip").forEach((chip) => {
      chip.disabled = state.sending;
    });
  }

  async function refreshVault() {
    if (!state.sessionId) return;
    try {
      const response = await fetch(`/v1/sessions/${encodeURIComponent(state.sessionId)}`);
      if (!response.ok) return;
      const body = await response.json();
      const turns = body.turn_count || 0;
      const commitments = body.commitment_count || 0;
      els.vault.textContent = body.conversation_id
        ? `SQLite · ${turns} turn${turns === 1 ? "" : "s"} · ${commitments} commitment${commitments === 1 ? "" : "s"}`
        : "No rows for this session yet.";
    } catch (_err) {
      els.vault.textContent = "Vault snapshot unavailable.";
    }
  }

  async function loadEvidence(commitmentId) {
    if (!commitmentId) return { available: false, detail: "This turn has no commitment id." };
    try {
      const response = await fetch(`/v1/evidence/${encodeURIComponent(commitmentId)}`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        return { available: false, detail: "No Vault row for this commitment yet." };
      }
      return Object.assign({ available: true }, body);
    } catch (_err) {
      return { available: false, detail: "Evidence lookup failed. The turn result is still shown." };
    }
  }

  async function sendTurn(text) {
    const utterance = (text ?? els.text.value).trim();
    const sessionId = els.session.value.trim();
    const speaker = els.speaker.value.trim();
    if (!sessionId || !speaker || !utterance) {
      setError("Session, speaker role, and text are required.");
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
        evidence: body.commitment && body.commitment.commitment_id ? { pending: true } : {
          available: false,
          detail: "This turn has no commitment id.",
        },
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

  function renderChips() {
    SAMPLES.forEach((sample) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chip";
      const title = document.createElement("strong");
      title.textContent = sample.label;
      const expect = document.createElement("span");
      expect.textContent = sample.expect;
      button.append(title, expect);
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

  renderChips();
  const stored = loadStore();
  adoptSession(stored.current || id("sess"));

  fetch("/health")
    .then((response) => response.json())
    .then((body) => {
      els.health.textContent = body.status === "ok" ? "Brain ok · fixture mode" : "Brain unavailable";
    })
    .catch(() => {
      els.health.textContent = "Brain unavailable";
    });

  fetch("/v1/demo/status")
    .then((response) => response.json())
    .then((body) => {
      els.foot.textContent = `Fixture mode · no API keys · ${body.db_path || "sqlite"}`;
    })
    .catch(() => {});
})();

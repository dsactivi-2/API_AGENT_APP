// Agent Chat – Oberfläche
(function () {
  const TOKEN = new URLSearchParams(location.hash.slice(1)).get("token") || sessionStorage.getItem("agentchat-token") || "";
  if (TOKEN) { try { sessionStorage.setItem("agentchat-token", TOKEN); } catch (_) {} history.replaceState(null, "", "/"); }

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  const state = { chats: [], current: null, pending: [], running: new Set(), es: null, settings: null };

  async function api(path, opts = {}) {
    const headers = { "X-AgentChat-Token": TOKEN, ...(opts.headers || {}) };
    if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
    const res = await fetch(path, { ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `HTTP ${res.status}`);
    return data;
  }

  // --- Chat-Liste -------------------------------------------------------------
  async function loadChats() {
    const q = $("search").value.trim();
    state.chats = (await api(`/api/chats?q=${encodeURIComponent(q)}`)).chats;
    if (state.current) {
      const cur = state.chats.find((c) => c.id === state.current.id);
      if (cur) { state.current = cur; $("chatTitle").textContent = cur.title; }
    }
    renderChatList();
  }

  function groupLabel(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 864e5);
    if (days === 0) return "Heute"; if (days === 1) return "Gestern"; if (days < 7) return "Letzte 7 Tage";
    if (days < 30) return "Letzte 30 Tage"; return d.toLocaleDateString("de-DE", { month: "long", year: "numeric" });
  }

  function renderChatList() {
    const list = $("chatList"); list.innerHTML = "";
    let last = null;
    for (const c of state.chats) {
      const g = groupLabel(c.updated_at);
      if (g !== last) { list.append(el("div", "chat-group", g)); last = g; }
      const b = el("button", "chat-item" + (state.current && state.current.id === c.id ? " active" : ""));
      if (state.running.has(c.id)) b.append(el("span", "running", "●"));
      b.append(document.createTextNode(c.title)); b.title = c.title;
      b.onclick = () => openChat(c.id);
      list.append(b);
    }
    if (!state.chats.length) list.append(el("div", "muted small", $("search").value ? "Keine Treffer." : "Noch keine Chats."));
  }

  // --- Chat anzeigen --------------------------------------------------------------
  async function openChat(id) {
    closeStream();
    const data = await api(`/api/chats/${id}`);
    state.current = data.chat;
    $("chatTitle").textContent = data.chat.title;
    $("renameChat").hidden = $("deleteChat").hidden = false;
    const box = $("messages"); box.innerHTML = "";
    for (const m of data.messages) box.append(renderMessage(m));
    if (!data.messages.length) box.append(emptyState());
    renderChatList();
    setRunning(data.running);
    if (data.running) attachStream(id, startAssistant());
    scrollDown(true);
    $("input").focus();
  }

  const emptyTemplate = $("empty").cloneNode(true);
  const emptyState = () => emptyTemplate.cloneNode(true);

  function renderMessage(m) {
    const wrap = el("div", `msg ${m.role}`);
    if (m.attachments && m.attachments.length) {
      const al = el("div", "att-list");
      for (const a of m.attachments) al.append(attChip(a, false));
      wrap.append(al);
    }
    if (m.role === "assistant" && m.activity && m.activity.length) wrap.append(activityBox(m.activity));
    const bubble = el("div", "bubble");
    if (m.role === "assistant") { bubble.innerHTML = window.renderMarkdown(m.content); enhance(bubble); }
    else bubble.textContent = m.content;
    if (m.content) wrap.append(bubble);
    if (m.role === "assistant" && m.content) wrap.append(msgActions(m.content));
    return wrap;
  }

  function msgActions(text) {
    const row = el("div", "msg-actions");
    const copy = el("button", "ghost", "Kopieren");
    copy.onclick = () => copyText(text, copy);
    row.append(copy);
    return row;
  }

  function activityBox(items) {
    const box = el("div", "activity");
    for (const a of items) box.append(chip(a));
    return box;
  }

  function chip(a) {
    const c = el("span", `chip ${a.kind === "error" ? "error" : (a.status || "completed")}`, a.label + (a.detail ? ` – ${a.detail.length > 110 ? a.detail.slice(0, 110) + "…" : a.detail}` : ""));
    c.title = a.detail || a.label; if (a.id) c.dataset.id = a.id;
    return c;
  }

  function attChip(a, removable) {
    const c = el("span", "att");
    if (a.mime && a.mime.startsWith("image/")) {
      const img = el("img"); img.alt = a.name;
      fetch(`/api/uploads/${a.id}`, { headers: { "X-AgentChat-Token": TOKEN } }).then((r) => r.blob()).then((b) => { img.src = URL.createObjectURL(b); }).catch(() => {});
      c.append(img);
    }
    c.append(document.createTextNode(`${a.name} (${fmtSize(a.size)})`));
    if (removable) {
      const x = el("button", "", "✕"); x.title = "Entfernen";
      x.onclick = () => { state.pending = state.pending.filter((p) => p.id !== a.id); renderPending(); };
      c.append(x);
    }
    return c;
  }

  const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;

  function enhance(root) {
    root.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".copy-btn")) return;
      const b = el("button", "copy-btn", "Kopieren");
      b.onclick = () => copyText(pre.querySelector("code").textContent, b);
      pre.append(b);
    });
  }

  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); }
    catch (_) { const t = el("textarea"); t.value = text; document.body.append(t); t.select(); document.execCommand("copy"); t.remove(); }
    const old = btn.textContent; btn.textContent = "Kopiert ✓"; setTimeout(() => (btn.textContent = old), 1200);
  }

  function scrollDown(force) {
    const box = $("messages");
    if (force || box.scrollHeight - box.scrollTop - box.clientHeight < 160) box.scrollTop = box.scrollHeight;
  }

  // --- Senden & Streaming ------------------------------------------------------------
  function setRunning(on) {
    $("send").hidden = on; $("stop").hidden = !on;
    if (state.current) { on ? state.running.add(state.current.id) : state.running.delete(state.current.id); }
    renderChatList();
  }

  function startAssistant() {
    const wrap = el("div", "msg assistant");
    const ui = {
      wrap,
      activity: el("div", "activity"),
      reasoningWrap: null, reasoning: null,
      bubble: el("div", "bubble typing"),
      text: "",
    };
    wrap.append(ui.activity, ui.bubble);
    $("messages").append(wrap);
    scrollDown(true);
    return ui;
  }

  async function send() {
    const text = $("input").value.trim();
    if (!text && !state.pending.length) return;
    if (!state.current) {
      const chat = await api("/api/chats", { method: "POST" });
      state.current = chat;
      $("messages").innerHTML = "";
      $("renameChat").hidden = $("deleteChat").hidden = false;
    }
    const empty = $("messages").querySelector(".empty"); if (empty) empty.remove();
    const attachments = state.pending.slice();
    $("messages").append(renderMessage({ role: "user", content: text, attachments }));
    $("input").value = ""; autosize(); state.pending = []; renderPending();
    const chatId = state.current.id;
    try {
      await api(`/api/chats/${chatId}/send`, { method: "POST", json: { text, attachments: attachments.map((a) => a.id) } });
    } catch (e) {
      $("messages").append(noticeEl("Senden fehlgeschlagen: " + e.message, true));
      return;
    }
    setRunning(true);
    attachStream(chatId, startAssistant());
    loadChats();
  }

  function closeStream() { if (state.es) { state.es.close(); state.es = null; } }

  function attachStream(chatId, ui) {
    closeStream();
    const es = new EventSource(`/api/chats/${chatId}/events?token=${encodeURIComponent(TOKEN)}`);
    state.es = es;
    const pending = { raf: 0 };
    const paint = () => { pending.raf = 0; ui.bubble.innerHTML = window.renderMarkdown(ui.text); enhance(ui.bubble); scrollDown(); };
    es.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      if (!state.current || state.current.id !== chatId) return;
      switch (ev.type) {
        case "text":
          ui.text += ev.delta;
          if (!pending.raf) pending.raf = requestAnimationFrame(paint);
          break;
        case "reasoning":
          if (!ui.reasoning) {
            ui.reasoningWrap = el("details", "reasoning-wrap"); ui.reasoningWrap.open = true;
            ui.reasoningWrap.append(el("summary", "", "Überlegung"));
            ui.reasoning = el("div", "reasoning"); ui.reasoningWrap.append(ui.reasoning);
            ui.wrap.insertBefore(ui.reasoningWrap, ui.bubble);
          }
          ui.reasoning.textContent += ev.delta; scrollDown();
          break;
        case "activity": ui.activity.append(chip(ev)); scrollDown(); break;
        case "activity_done": {
          const c = ui.activity.querySelector(`[data-id="${CSS.escape(ev.id || "")}"]`);
          if (c) c.className = `chip ${ev.status}`;
          break;
        }
        case "approval": ui.wrap.insertBefore(approvalCard(chatId, ev), ui.bubble); scrollDown(true); notify("Bestätigung nötig", `${ev.folder}/${ev.path}`); break;
        case "approval_resolved": {
          const card = ui.wrap.querySelector(`.approval[data-id="${ev.id}"]`);
          if (card) { card.classList.add("resolved"); card.querySelector(".row").replaceWith(el("div", ev.approved ? "ok small" : "error small", ev.approved ? "Freigegeben" : "Abgelehnt")); }
          break;
        }
        case "notice": ui.wrap.insertBefore(noticeEl(ev.text), ui.bubble); break;
        case "error": ui.wrap.append(noticeEl("Fehler: " + ev.text, true)); break;
        case "done":
          es.close(); state.es = null;
          if (pending.raf) cancelAnimationFrame(pending.raf);
          paint(); ui.bubble.classList.remove("typing");
          if (!ui.text) ui.bubble.remove(); else ui.wrap.append(msgActions(ui.text));
          setRunning(false); loadChats();
          if (document.hidden) notify("Antwort fertig", state.current ? state.current.title : "");
          break;
      }
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) { setRunning(false); }
    };
  }

  function noticeEl(text, isError) { return el("div", "notice" + (isError ? " error" : ""), text); }

  function approvalCard(chatId, ev) {
    const card = el("div", "approval"); card.dataset.id = ev.id;
    const verb = ev.mode === "append" ? "anhängen an" : ev.exists ? "überschreiben" : "neu anlegen";
    card.append(el("h4", "", `Agent möchte ${verb}: ${ev.folder}/${ev.path}`));
    const diff = el("div", "diff");
    for (const line of (ev.diff || "").split("\n")) {
      const cls = line.startsWith("+++") || line.startsWith("---") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "";
      diff.append(el("div", cls, line || " "));
    }
    const row = el("div", "row");
    const yes = el("button", "primary", "Schreiben erlauben"), no = el("button", "danger", "Ablehnen");
    const decide = async (approved) => {
      yes.disabled = no.disabled = true;
      try { await api(`/api/chats/${chatId}/approvals/${ev.id}`, { method: "POST", json: { approved } }); }
      catch (e) { row.append(el("span", "error small", e.message)); yes.disabled = no.disabled = false; }
    };
    yes.onclick = () => decide(true); no.onclick = () => decide(false);
    row.append(yes, no);
    card.append(diff, row);
    return card;
  }

  function notify(title, body) {
    try {
      if (!document.hidden || !("Notification" in window)) return;
      if (Notification.permission === "granted") new Notification(title, { body });
      else if (Notification.permission !== "denied") Notification.requestPermission();
    } catch (_) {}
  }

  // --- Uploads ---------------------------------------------------------------------
  async function addFiles(fileList) {
    for (const f of fileList) {
      const fd = new FormData(); fd.append("file", f, f.name || "bild.png");
      try {
        const res = await fetch("/api/uploads", { method: "POST", body: fd, headers: { "X-AgentChat-Token": TOKEN } });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        state.pending.push(data); renderPending();
      } catch (e) { alert(`Upload fehlgeschlagen (${f.name}): ${e.message}`); }
    }
  }

  function renderPending() {
    const box = $("attachments"); box.innerHTML = "";
    for (const a of state.pending) box.append(attChip(a, true));
  }

  // --- Einstellungen ---------------------------------------------------------------
  async function openSettings() {
    const data = await api("/api/settings");
    state.settings = data.settings;
    const s = data.settings;
    $("agentId").value = s.agent_id; $("baseUrl").value = s.base_url || "https://api.openai.com/v1"; $("modelOverride").value = s.model_override || ""; $("reasoningEffort").value = s.reasoning_effort || "";
    $("spendLimit").value = s.spend_limit_cents || ""; $("showReasoning").checked = s.show_reasoning;
    $("localTools").checked = s.local_tools; $("apiKey").value = ""; $("testResult").textContent = "";
    $("settingsError").textContent = "";
    renderKeyStatus(data.api_key);
    const box = $("folders"); box.innerHTML = "";
    for (const f of s.folders) box.append(folderRow(f));
    $("settings").showModal();
  }

  function renderKeyStatus(k) {
    $("apiKeyStatus").innerHTML = "";
    const ok = k.set && k.looks_valid;
    $("apiKeyStatus").append(!k.set ? el("span", "error", "Kein API-Key gespeichert.")
      : ok ? el("span", "ok", `Gespeichert: ${k.masked}`)
      : el("span", "error", `Gespeichert ist ${k.masked}. Das ist kein OpenAI-Key (sk-…). Bitte „Entfernen“ und neu eintragen.`));
    $("keyState").textContent = ok ? "API-Key: gesetzt" : "API-Key fehlt";
    $("keyState").className = "small " + (ok ? "muted" : "error");
  }

  function folderRow(f = { name: "", path: "", mode: "read" }) {
    const row = el("div", "folder-row");
    const name = el("input"); name.type = "text"; name.placeholder = "Name, z. B. lina"; name.value = f.name; name.className = "f-name";
    const path = el("input"); path.type = "text"; path.placeholder = "/Users/…/Projekt"; path.value = f.path; path.className = "f-path"; path.spellcheck = false;
    const pick = el("button", "ghost", "Wählen…"); pick.type = "button";
    pick.onclick = async () => {
      if (window.pywebview && window.pywebview.api) {
        const p = await window.pywebview.api.pick_folder();
        if (p) { path.value = p; if (!name.value) name.value = p.split("/").filter(Boolean).pop().replace(/[^\w-]/g, "_"); }
      } else alert("Ordnerauswahl nur im App-Fenster verfügbar. Pfad bitte eintippen.");
    };
    const mode = el("select"); mode.className = "f-mode";
    for (const [v, l] of [["read", "Nur lesen"], ["readwrite", "Lesen + Schreiben"]]) { const o = el("option", "", l); o.value = v; mode.append(o); }
    mode.value = f.mode;
    const del = el("button", "ghost danger", "✕"); del.type = "button"; del.title = "Entfernen"; del.onclick = () => row.remove();
    row.append(name, path, pick, mode, del);
    return row;
  }

  async function saveSettings() {
    const folders = [...document.querySelectorAll(".folder-row")].map((r) => ({
      name: r.querySelector(".f-name").value.trim(), path: r.querySelector(".f-path").value.trim(), mode: r.querySelector(".f-mode").value,
    })).filter((f) => f.name || f.path);
    const body = {
      agent_id: $("agentId").value.trim(), base_url: $("baseUrl").value.trim(), model_override: $("modelOverride").value.trim(), reasoning_effort: $("reasoningEffort").value,
      spend_limit_cents: $("spendLimit").value ? Number($("spendLimit").value) : null,
      show_reasoning: $("showReasoning").checked, local_tools: $("localTools").checked, folders,
    };
    try {
      await api("/api/settings", { method: "PUT", json: body });
      $("settings").close();
    } catch (e) { $("settingsError").textContent = e.message; }
  }

  // --- Events ----------------------------------------------------------------------
  function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 240) + "px"; }

  $("input").addEventListener("input", autosize);
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$("send").hidden) send(); }
  });
  $("input").addEventListener("paste", (e) => {
    const filesIn = [...(e.clipboardData?.files || [])];
    if (filesIn.length) { e.preventDefault(); addFiles(filesIn); }
  });
  $("send").onclick = send;
  $("stop").onclick = async () => { if (state.current) { try { await api(`/api/chats/${state.current.id}/stop`, { method: "POST" }); } catch (_) {} } };
  $("fileInput").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
  $("newChat").onclick = () => {
    closeStream(); state.current = null; $("chatTitle").textContent = "Agent Chat";
    $("renameChat").hidden = $("deleteChat").hidden = true;
    $("messages").innerHTML = ""; $("messages").append(emptyTemplate.cloneNode(true));
    $("send").hidden = false; $("stop").hidden = true;
    renderChatList(); $("input").focus();
  };
  let searchTimer; $("search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadChats, 200); };
  $("renameChat").onclick = async () => {
    const t = prompt("Neuer Titel:", state.current.title); if (!t) return;
    state.current = await api(`/api/chats/${state.current.id}`, { method: "PATCH", json: { title: t } });
    $("chatTitle").textContent = state.current.title; loadChats();
  };
  $("deleteChat").onclick = async () => {
    if (!confirm(`Chat „${state.current.title}“ löschen? Das kann nicht rückgängig gemacht werden.`)) return;
    try { await api(`/api/chats/${state.current.id}`, { method: "DELETE" }); } catch (e) { alert(e.message); return; }
    $("newChat").onclick(); loadChats();
  };
  $("openSettings").onclick = openSettings;
  $("saveSettings").onclick = saveSettings;
  $("addFolder").onclick = () => $("folders").append(folderRow());
  $("saveKey").onclick = async () => {
    try { renderKeyStatus(await api("/api/api-key", { method: "PUT", json: { api_key: $("apiKey").value } })); $("apiKey").value = ""; }
    catch (e) { $("apiKeyStatus").textContent = ""; $("apiKeyStatus").append(el("span", "error", e.message)); }
  };
  $("deleteKey").onclick = async () => { if (confirm("API-Key aus dem Schlüsselbund entfernen?")) renderKeyStatus(await api("/api/api-key", { method: "DELETE" })); };
  $("loadAgents").onclick = async () => {
    const sel = $("agentSelect");
    try {
      const { agents } = await api("/api/agents");
      sel.innerHTML = "";
      const first = el("option", "", agents.length ? `${agents.length} Agent(s) gefunden – auswählen …` : "Keine Agents in diesem Projekt");
      first.value = ""; sel.append(first);
      for (const a of agents) { const o = el("option", "", `${a.name || "(ohne Name)"} · ${a.model} · ${a.id}`); o.value = a.id; sel.append(o); }
      sel.hidden = false;
    } catch (e) { $("testResult").textContent = e.message; $("testResult").className = "small error"; }
  };
  $("createDesigner").onclick = async () => {
    if (!confirm("Den Voice-Agent-Designer (gpt-6-astra, Web-Suche, OpenAI-Docs-MCP, inspect_project) im Projekt des aktuellen API-Keys neu anlegen?\n\nGitHub-MCP wird weggelassen, weil dessen Zugang zum alten Projekt gehört.")) return;
    $("testResult").textContent = "Lege Agent an …"; $("testResult").className = "small muted";
    try {
      const r = await api("/api/agents/create-designer", { method: "POST" });
      $("agentId").value = r.agent.id;
      $("testResult").textContent = `Angelegt und ausgewählt: ${r.agent.name} (${r.agent.id}). Jetzt „Verbindung testen“.`;
      $("testResult").className = "small ok";
    } catch (e) { $("testResult").textContent = e.message; $("testResult").className = "small error"; }
  };
  $("agentSelect").onchange = (e) => { if (e.target.value) { $("agentId").value = e.target.value; $("testResult").textContent = "Agent übernommen – jetzt unten „Speichern“, dann testen."; $("testResult").className = "small muted"; } };
  $("testConn").onclick = async () => {
    $("testResult").textContent = "Teste …"; $("testResult").className = "small muted";
    try {
      const r = await api("/api/test-connection", { method: "POST" });
      $("testResult").textContent = `OK: ${r.agent.name || r.agent.id} · Modell ${r.agent.model} · Reasoning ${r.agent.reasoning_effort} · Tools: ${r.agent.tools.join(", ")}`;
      $("testResult").className = "small ok";
    } catch (e) { $("testResult").textContent = e.message; $("testResult").className = "small error"; }
  };

  const comp = $("composer");
  ["dragenter", "dragover"].forEach((t) => document.addEventListener(t, (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); comp.classList.add("dragging"); $("dropHint").hidden = false; } }));
  ["dragleave", "drop"].forEach((t) => document.addEventListener(t, (e) => {
    if (t === "dragleave" && e.relatedTarget) return;
    comp.classList.remove("dragging"); $("dropHint").hidden = true;
  }));
  document.addEventListener("drop", (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); addFiles([...e.dataTransfer.files]); } });

  // --- Start -----------------------------------------------------------------------
  (async () => {
    if (!TOKEN) { document.body.innerHTML = "<p style='padding:40px'>Bitte die App über „Agent Chat.app“ starten.</p>"; return; }
    const data = await api("/api/settings");
    renderKeyStatus(data.api_key);
    await loadChats();
    if (!data.api_key.set || !data.api_key.looks_valid || !data.settings.agent_id) openSettings();
    $("input").focus();
  })().catch((e) => alert("Start fehlgeschlagen: " + e.message));
})();

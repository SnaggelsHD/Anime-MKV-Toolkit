// Chapter analyzer: detects Prologue/Opening/Episode/Ending/Epilogue chapters
// by matching episode audio against animethemes.moe OP/ED songs. Reached via
// the "Analyze Chapters" button on a season or an episode (see renderEpisodes()
// and renderEpisodeDetail() in app.js) rather than its own library browser -
// this file assumes app.js has already run (state/api/toast/escapeHtml/
// parseChapterAtoms/formatChapterTime are all globals it defines).

const CHAPTERIZE_CHAPTER_TYPES = ["prologue", "opening", "episode", "ending", "epilogue", "end"];
// "episode" isn't configurable - its title is always plain "Episode" (see
// jobs.py), so it's excluded from the naming schema settings form.
const CHAPTERIZE_NAMING_TYPES = CHAPTERIZE_CHAPTER_TYPES.filter((t) => t !== "episode");
const CHAPTERIZE_LAST_JOB_KEY = "chapterize_last_job_id";

const chapterizeState = {
  show: null,
  episodes: [], // [{id, filename, season, episode}]
  episodeNumberOverrides: {}, // episode_id -> number
  selectedAnime: null, // {slug, name}
  selectedThemeSlugs: new Set(),
  jobId: null,
  resultEpisodes: null,
  previewOpenIndices: new Set(),
  chapterButtonRefs: {},
};

let chapterizeEventSource = null;

// ---- Floating progress chip (visible when analysis runs outside the view) ----

// Whether the chip should be shown at all (independent of view visibility).
let chapterizeChipActive = false;

// Sync chip visibility: show only when active AND not on the chapterize view.
function syncChapterizeChip() {
  const chip = document.getElementById("chapterize-chip");
  const onView = document.getElementById("view-chapterize").style.display !== "none";
  chip.hidden = !chapterizeChipActive || onView;
}

function activateChapterizeChip(label, progressPct, statusText, done) {
  chapterizeChipActive = true;
  document.getElementById("chapterize-chip-label").textContent = label || "Analyzing...";
  document.getElementById("chapterize-chip-fill").style.width = `${progressPct ?? 0}%`;
  document.getElementById("chapterize-chip-status").textContent = statusText || "";
  document.getElementById("chapterize-chip-dismiss").hidden = !done;
  syncChapterizeChip();
}

function deactivateChapterizeChip() {
  chapterizeChipActive = false;
  document.getElementById("chapterize-chip").hidden = true;
}

document.getElementById("chapterize-chip").addEventListener("click", () => {
  document.getElementById("view-library").style.display = "none";
  document.getElementById("view-settings").style.display = "none";
  document.getElementById("view-chapterize").style.display = "";
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
  syncChapterizeChip();
});

document.getElementById("chapterize-chip-dismiss").addEventListener("click", (e) => {
  e.stopPropagation();
  deactivateChapterizeChip();
});

function fmtTime(seconds) {
  seconds = Math.max(0, seconds || 0);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
}

function parseTime(text) {
  const parts = String(text).trim().split(":").map(Number);
  if (parts.some((p) => Number.isNaN(p))) return null;
  let seconds = 0;
  for (const p of parts) seconds = seconds * 60 + p;
  return seconds;
}

// ---- Entry / navigation ----

function openChapterizeView(show, episodes) {
  if (!show || !episodes || episodes.length === 0) {
    toast("No episodes to analyze", "error");
    return;
  }
  if (chapterizeEventSource) {
    chapterizeEventSource.close();
    chapterizeEventSource = null;
  }
  deactivateChapterizeChip();
  chapterizeState.show = show;
  chapterizeState.episodes = episodes.slice().sort((a, b) => a.filename.localeCompare(b.filename));
  chapterizeState.episodeNumberOverrides = {};
  chapterizeState.selectedAnime = null;
  chapterizeState.selectedThemeSlugs = new Set();
  chapterizeState.jobId = null;
  chapterizeState.resultEpisodes = null;
  chapterizeState.previewOpenIndices = new Set();
  chapterizeState.chapterButtonRefs = {};

  document.getElementById("view-library").style.display = "none";
  document.getElementById("view-settings").style.display = "none";
  document.getElementById("view-chapterize").style.display = "";
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));

  document.getElementById("chapterize-results-panel").style.display = "none";
  document.getElementById("chapterize-progress-wrap").hidden = true;
  document.getElementById("chapterize-anime-results").innerHTML = "";
  document.getElementById("chapterize-theme-picker").innerHTML = "";
  document.getElementById("chapterize-mode-match-all").checked = true;
  updateChapterizeStartButton();

  const heading = episodes.length === 1 ? `Analyze Chapters: ${episodes[0].filename}` : `Analyze Chapters: ${show.name}`;
  document.getElementById("chapterize-heading").textContent = heading;
  document.getElementById("chapterize-episode-summary").textContent =
    `${episodes.length} episode(s) selected from "${show.name}". The # is used for {episode} in chapter ` +
    `titles and for per-episode OP/ED assignment - fix it if it's wrong.`;

  renderChapterizeEpisodeList();

  const searchInput = document.getElementById("chapterize-anime-search");
  searchInput.value = show.name;
  searchChapterizeAnime();
}

function closeChapterizeView() {
  if (chapterizeEventSource) {
    chapterizeEventSource.close();
    chapterizeEventSource = null;
  }
  document.getElementById("view-chapterize").style.display = "none";
  document.getElementById("view-library").style.display = "";
  document.querySelector('.tab-btn[data-tab="library"]')?.classList.add("active");
  syncChapterizeChip();
}

document.getElementById("chapterize-back").addEventListener("click", closeChapterizeView);

// ---- Episode list (pre-selected, editable episode numbers) ----

function renderChapterizeEpisodeList() {
  const container = document.getElementById("chapterize-episode-list");
  container.innerHTML = chapterizeState.episodes
    .map(
      (ep, i) => `
      <div class="chapterize-episode-row">
        <input type="checkbox" checked data-episode-id="${ep.id}" class="chapterize-episode-checkbox">
        <span class="item-sub">#</span>
        <input type="number" min="0" class="chapterize-episode-number" data-episode-id="${ep.id}"
          value="${ep.episode != null ? Number(ep.episode) : i + 1}">
        <span>${escapeHtml(ep.filename)}</span>
      </div>`
    )
    .join("");

  chapterizeState.episodes.forEach((ep, i) => {
    chapterizeState.episodeNumberOverrides[ep.id] = ep.episode != null ? Number(ep.episode) : i + 1;
  });

  container.querySelectorAll(".chapterize-episode-number").forEach((input) =>
    input.addEventListener("change", () => {
      const v = parseInt(input.value, 10);
      if (!Number.isNaN(v)) chapterizeState.episodeNumberOverrides[Number(input.dataset.episodeId)] = v;
    })
  );
  container.querySelectorAll(".chapterize-episode-checkbox").forEach((cb) =>
    cb.addEventListener("change", updateChapterizeStartButton)
  );
}

function chapterizeSelectedEpisodeIds() {
  return Array.from(document.querySelectorAll(".chapterize-episode-checkbox:checked")).map((cb) =>
    Number(cb.dataset.episodeId)
  );
}

// ---- animethemes.moe search + theme selection ----

async function searchChapterizeAnime() {
  const q = document.getElementById("chapterize-anime-search").value.trim();
  const resultsEl = document.getElementById("chapterize-anime-results");
  document.getElementById("chapterize-theme-picker").innerHTML = "";
  chapterizeState.selectedAnime = null;
  chapterizeState.selectedThemeSlugs = new Set();
  updateChapterizeStartButton();
  if (!q) return;
  resultsEl.innerHTML = '<p class="item-sub">Searching...</p>';
  try {
    const anime = await api(`/api/chapterize/animethemes/search?q=${encodeURIComponent(q)}`);
    if (!anime.length) {
      resultsEl.innerHTML = '<p class="item-sub">No matches.</p>';
      return;
    }
    resultsEl.innerHTML = anime
      .map((a, i) => {
        const year = a.year ? ` (${a.year}${a.season ? " " + a.season : ""})` : "";
        return `<div class="anime-card" data-index="${i}">${escapeHtml(a.name + year)}</div>`;
      })
      .join("");
    resultsEl.querySelectorAll(".anime-card").forEach((card, i) =>
      card.addEventListener("click", () => selectChapterizeAnime(anime[i], card))
    );
  } catch (err) {
    resultsEl.innerHTML = `<p class="item-sub">Search failed: ${escapeHtml(err.message)}</p>`;
  }
}

document.getElementById("chapterize-anime-search-btn").addEventListener("click", searchChapterizeAnime);
document.getElementById("chapterize-anime-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchChapterizeAnime();
});

async function selectChapterizeAnime(anime, cardEl) {
  document.querySelectorAll(".anime-card").forEach((c) => c.classList.remove("selected"));
  cardEl.classList.add("selected");
  chapterizeState.selectedAnime = anime;
  chapterizeState.selectedThemeSlugs = new Set();
  const picker = document.getElementById("chapterize-theme-picker");
  picker.innerHTML = '<p class="item-sub">Loading themes...</p>';
  try {
    const themes = await api(`/api/chapterize/animethemes/${encodeURIComponent(anime.slug)}/themes`);
    if (!themes.length) {
      picker.innerHTML = '<p class="item-sub">No themes found for this title.</p>';
      updateChapterizeStartButton();
      return;
    }
    picker.innerHTML = themes
      .map((t) => {
        const range = t.episodes ? ` <span class="item-sub">(ep ${escapeHtml(t.episodes)})</span>` : "";
        const warn = t.has_video ? "" : ' <span class="item-sub">(no source available)</span>';
        if (t.has_video) chapterizeState.selectedThemeSlugs.add(t.slug);
        return `
        <label class="theme-row">
          <input type="checkbox" data-slug="${escapeHtml(t.slug)}" ${t.has_video ? "checked" : "disabled"}>
          <span class="badge ${t.type === "OP" ? "opening" : "ending"}">${escapeHtml(t.slug)}</span>
          ${escapeHtml(t.song_title || "(unknown song)")}${range}${warn}
        </label>`;
      })
      .join("");
    picker.querySelectorAll("input[data-slug]").forEach((cb) =>
      cb.addEventListener("change", () => {
        if (cb.checked) chapterizeState.selectedThemeSlugs.add(cb.dataset.slug);
        else chapterizeState.selectedThemeSlugs.delete(cb.dataset.slug);
        updateChapterizeStartButton();
      })
    );
  } catch (err) {
    picker.innerHTML = `<p class="item-sub">${escapeHtml(err.message)}</p>`;
  }
  updateChapterizeStartButton();
}

function updateChapterizeStartButton() {
  const btn = document.getElementById("chapterize-start-btn");
  btn.disabled =
    chapterizeSelectedEpisodeIds().length === 0 ||
    !chapterizeState.selectedAnime ||
    chapterizeState.selectedThemeSlugs.size === 0;
}

// ---- Analysis run: start, progress/log streaming ----

document.getElementById("chapterize-start-btn").addEventListener("click", startChapterizeAnalysis);
document.getElementById("chapterize-cancel-btn").addEventListener("click", cancelChapterizeAnalysis);

async function startChapterizeAnalysis() {
  const episodeIds = chapterizeSelectedEpisodeIds();
  if (episodeIds.length === 0 || !chapterizeState.selectedAnime || chapterizeState.selectedThemeSlugs.size === 0) {
    return;
  }
  const overrides = {};
  for (const id of episodeIds) overrides[id] = chapterizeState.episodeNumberOverrides[id];

  const payload = {
    episode_ids: episodeIds,
    anime_slug: chapterizeState.selectedAnime.slug,
    anime_name: chapterizeState.selectedAnime.name,
    theme_slugs: Array.from(chapterizeState.selectedThemeSlugs),
    mode: document.querySelector('input[name="chapterize-mode"]:checked').value,
    episode_number_overrides: overrides,
  };

  document.getElementById("chapterize-results-panel").style.display = "none";
  resetChapterizeProgressUI();

  let res;
  try {
    res = await api("/api/chapterize/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    appendChapterizeLog(`Failed to start analysis: ${err.message}`, "err");
    document.getElementById("chapterize-start-btn").disabled = false;
    return;
  }
  localStorage.setItem(CHAPTERIZE_LAST_JOB_KEY, res.job_id);
  attachToChapterizeJob(res.job_id);
}

function resetChapterizeProgressUI() {
  document.getElementById("chapterize-start-btn").disabled = true;
  document.getElementById("chapterize-cancel-btn").hidden = false;
  document.getElementById("chapterize-cancel-btn").disabled = false;
  document.getElementById("chapterize-progress-wrap").hidden = false;
  document.getElementById("chapterize-log").textContent = "";
  document.getElementById("chapterize-progress-fill").style.width = "0%";
  document.getElementById("chapterize-progress-status").textContent = "";
}

function attachToChapterizeJob(jobId) {
  chapterizeState.jobId = jobId;
  const startBtn = document.getElementById("chapterize-start-btn");
  const cancelBtn = document.getElementById("chapterize-cancel-btn");
  const fillEl = document.getElementById("chapterize-progress-fill");
  const statusEl = document.getElementById("chapterize-progress-status");

  if (chapterizeEventSource) chapterizeEventSource.close();
  chapterizeEventSource = new EventSource(`/api/chapterize/analyze/${jobId}/events`);

  // Set to true once a terminal status arrives so the onerror handler
  // knows not to reconnect (job already finished).
  let jobDone = false;

  chapterizeEventSource.addEventListener("log", (ev) => {
    const entries = JSON.parse(ev.data);
    entries.forEach((e) => appendChapterizeLog(e.message, e.level));
  });
  chapterizeEventSource.addEventListener("status", (ev) => {
    const status = JSON.parse(ev.data);
    fillEl.style.width = `${status.progress}%`;
    const statusText =
      status.status === "queued" ? "Queued - waiting for another analysis to finish..." :
      status.status === "cancelled" ? "Cancelled." :
      status.status === "running" ? `Analyzing ${status.season_label || ""}...` : "";
    statusEl.textContent = statusText;

    const showName = chapterizeState.show ? chapterizeState.show.name : "Chapter analysis";
    const terminal = ["done", "error", "cancelled"].includes(status.status);
    activateChapterizeChip(
      terminal
        ? (status.status === "done" ? `✓ ${showName}` : `${showName} — ${status.status}`)
        : `Analyzing: ${showName}`,
      status.progress,
      terminal ? "Click to view results" : statusText,
      terminal,
    );

    if (terminal) {
      jobDone = true;
      chapterizeEventSource.close();
      chapterizeEventSource = null;
      startBtn.disabled = false;
      cancelBtn.hidden = true;
      if (status.status === "error") {
        appendChapterizeLog(`Job failed: ${status.error}`, "err");
      } else if (status.episodes && status.episodes.length) {
        chapterizeState.resultEpisodes = status.episodes;
        renderChapterizeResults();
      }
    }
  });
  chapterizeEventSource.onerror = () => {
    if (jobDone) return;
    if (chapterizeEventSource && chapterizeEventSource.readyState === EventSource.CLOSED) {
      // Browser gave up reconnecting. Probe the result endpoint first: if the
      // job is gone (server restart wiped in-memory jobs) stop looping and
      // report the loss; if it still exists reconnect normally.
      chapterizeEventSource = null;
      appendChapterizeLog("Stream closed - checking job status...", "warn");
      setTimeout(async () => {
        if (chapterizeState.jobId !== jobId || jobDone) return;
        try {
          await api(`/api/chapterize/analyze/${jobId}/result`);
          // Job still exists; reconnect.
          appendChapterizeLog("Reconnecting to analysis stream...");
          attachToChapterizeJob(jobId);
        } catch (_) {
          // Job gone (404) — server likely restarted and lost the in-memory job.
          appendChapterizeLog("Analysis job lost — the server may have restarted. Please start the analysis again.", "err");
          startBtn.disabled = false;
          cancelBtn.hidden = true;
          chapterizeState.jobId = null;
          localStorage.removeItem(CHAPTERIZE_LAST_JOB_KEY);
          deactivateChapterizeChip();
        }
      }, 3000);
    } else {
      // readyState === CONNECTING: browser is already auto-retrying.
      appendChapterizeLog("Connection interrupted - browser retrying...", "warn");
    }
  };
}

async function cancelChapterizeAnalysis() {
  if (!chapterizeState.jobId) return;
  const cancelBtn = document.getElementById("chapterize-cancel-btn");
  cancelBtn.disabled = true;
  document.getElementById("chapterize-progress-status").textContent = "Cancelling after the current episode...";
  try {
    await api(`/api/chapterize/analyze/${chapterizeState.jobId}/cancel`, { method: "POST" });
  } catch (err) {
    appendChapterizeLog(`Failed to request cancellation: ${err.message}`, "err");
    cancelBtn.disabled = false;
  }
}

// Reconnects to whatever job was last running/finished so a page refresh
// (or navigating away and back) doesn't strand an in-progress analysis.
async function resumeChapterizeJob() {
  const jobId = localStorage.getItem(CHAPTERIZE_LAST_JOB_KEY);
  if (!jobId) return;
  try {
    const data = await api(`/api/chapterize/analyze/${jobId}/result`);
    if (["running", "queued", "pending"].includes(data.status)) {
      chapterizeState.jobId = jobId;
      resetChapterizeProgressUI();
      appendChapterizeLog("Reconnected to an analysis already in progress.");
      activateChapterizeChip("Chapter analysis in progress", 0, "Reconnecting...", false);
      attachToChapterizeJob(jobId);
    } else if (data.status === "done" || (data.status === "cancelled" && data.episodes.length)) {
      chapterizeState.jobId = jobId;
      chapterizeState.resultEpisodes = data.episodes;
      renderChapterizeResults();
      activateChapterizeChip("✓ Chapter analysis complete", 100, "Click to view results", true);
    }
  } catch (err) {
    // Server unreachable or job gone; nothing to resume.
  }
}

function appendChapterizeLog(message, level) {
  const logEl = document.getElementById("chapterize-log");
  const line = document.createElement("div");
  line.className = level && level !== "info" ? `log-line ${level}` : "log-line";
  line.textContent = message;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

// ---- Results review: editable chapters, old-chapters comparison, preview ----

function renderChapterizeResults() {
  const panel = document.getElementById("chapterize-results-panel");
  const list = document.getElementById("chapterize-results-list");
  panel.style.display = "block";
  chapterizeState.chapterButtonRefs = {};

  list.innerHTML = "";
  chapterizeState.resultEpisodes.forEach((ep, epIndex) => {
    const card = document.createElement("div");
    card.className = "episode-card";
    const title = document.createElement("h3");
    title.textContent = `${ep.name}${ep.duration ? ` (${fmtTime(ep.duration)})` : ""}`;
    card.appendChild(title);

    if (ep.show_locked) {
      const lockNote = document.createElement("p");
      lockNote.className = "item-sub";
      lockNote.style.color = "var(--danger)";
      lockNote.textContent = "This show is locked - chapters detected here won't be saved for this episode.";
      card.appendChild(lockNote);
    }

    if (ep.error) {
      const err = document.createElement("p");
      err.className = "item-sub";
      err.style.color = "var(--danger)";
      err.textContent = `Analysis error: ${ep.error}`;
      card.appendChild(err);
    }

    if (ep.old_chapters_xml) {
      card.appendChild(buildOldChaptersBlock(ep.old_chapters_xml));
    }

    if (!ep.error) {
      const newHeading = document.createElement("h4");
      newHeading.className = "chapterize-subheading";
      newHeading.textContent = "New chapters (after analysis)";
      card.appendChild(newHeading);
    }

    const rowsWrap = document.createElement("div");
    ep.chapters.forEach((ch, chIndex) => renderChapterizeChapterRow(rowsWrap, ep, epIndex, ch, chIndex));
    card.appendChild(rowsWrap);

    const actionsRow = document.createElement("div");
    actionsRow.className = "item-actions";
    actionsRow.style.marginTop = "0.5rem";

    const addRow = document.createElement("button");
    addRow.type = "button";
    addRow.textContent = "+ Add chapter";
    addRow.onclick = () => {
      ep.chapters.push({ type: "episode", start: 0, end: ep.duration || 0, title: "New Chapter", confidence: null });
      ep.chapters.sort((a, b) => a.start - b.start);
      renderChapterizeResults();
    };
    actionsRow.appendChild(addRow);

    if (!ep.error && ep.duration) {
      const previewBtn = document.createElement("button");
      previewBtn.type = "button";
      previewBtn.textContent = chapterizeState.previewOpenIndices.has(epIndex) ? "Hide preview" : "Preview";
      previewBtn.onclick = () => {
        if (chapterizeState.previewOpenIndices.has(epIndex)) chapterizeState.previewOpenIndices.delete(epIndex);
        else chapterizeState.previewOpenIndices.add(epIndex);
        renderChapterizeResults();
      };
      actionsRow.appendChild(previewBtn);
    }
    card.appendChild(actionsRow);

    if (!ep.error && ep.duration && chapterizeState.previewOpenIndices.has(epIndex)) {
      card.appendChild(buildChapterizePreviewBlock(ep, epIndex));
    }

    list.appendChild(card);
  });
}

function buildOldChaptersBlock(oldChaptersXml) {
  const wrap = document.createElement("div");
  wrap.className = "old-chapters-panel";
  const heading = document.createElement("h4");
  heading.className = "chapterize-subheading";
  heading.textContent = "Existing chapters (before analysis)";
  wrap.appendChild(heading);

  const chapters = parseChapterAtoms(oldChaptersXml);
  if (chapters.length === 0) {
    const p = document.createElement("p");
    p.className = "item-sub";
    p.textContent = "Could not parse the existing chapters as a table.";
    wrap.appendChild(p);
    return wrap;
  }
  const table = document.createElement("table");
  table.innerHTML = `
    <thead><tr><th>#</th><th>Title</th><th>Start</th><th>End</th></tr></thead>
    <tbody>
      ${chapters
        .map(
          (c) => `<tr>
            <td>${c.index}</td>
            <td>${escapeHtml(c.title)}</td>
            <td>${escapeHtml(formatChapterTime(c.start))}</td>
            <td>${escapeHtml(formatChapterTime(c.end))}</td>
          </tr>`
        )
        .join("")}
    </tbody>`;
  wrap.appendChild(table);
  return wrap;
}

function buildChapterizePreviewBlock(ep, epIndex) {
  const wrap = document.createElement("div");
  wrap.className = "preview-block";

  const video = document.createElement("video");
  video.controls = true;
  video.preload = "metadata";
  video.src = `/api/chapterize/analyze/preview/${ep.episode_id}`;

  const loading = document.createElement("p");
  loading.className = "item-sub";
  loading.textContent = "Loading preview (a fresh copy of a non-H.264/AAC episode can take a little while to prepare)...";
  video.addEventListener("canplay", () => (loading.style.display = "none"), { once: true });
  video.addEventListener("error", () => {
    loading.textContent = "Couldn't load a preview for this episode.";
    loading.style.color = "var(--danger)";
  });

  const jumpButtons = document.createElement("div");
  jumpButtons.className = "chapter-jump-buttons";

  video.addEventListener("timeupdate", () => updateActiveChapterizeJumpButton(jumpButtons, ep, video.currentTime));

  chapterizeState.chapterButtonRefs[epIndex] = { buttonsEl: jumpButtons, video, ep };
  renderChapterizeJumpButtons(jumpButtons, ep, video);

  wrap.appendChild(video);
  wrap.appendChild(loading);
  wrap.appendChild(jumpButtons);
  return wrap;
}

function renderChapterizeJumpButtons(container, ep, video) {
  container.innerHTML = "";
  ep.chapters
    .slice()
    .sort((a, b) => a.start - b.start)
    .forEach((ch) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `chapter-jump-btn ${ch.type}`;
      btn.dataset.start = ch.start;
      const label = document.createElement("span");
      label.textContent = ch.title || ch.type;
      const time = document.createElement("span");
      time.className = "chapter-jump-time";
      time.textContent = fmtTime(ch.start);
      btn.appendChild(label);
      btn.appendChild(time);
      btn.onclick = () => {
        video.currentTime = ch.start;
        video.play().catch(() => {});
      };
      container.appendChild(btn);
    });
}

function updateActiveChapterizeJumpButton(container, ep, currentTime) {
  const sorted = ep.chapters.slice().sort((a, b) => a.start - b.start);
  let activeStart = null;
  for (const ch of sorted) {
    if (currentTime >= ch.start) activeStart = ch.start;
  }
  container.querySelectorAll(".chapter-jump-btn").forEach((btn) => {
    btn.classList.toggle("active", activeStart !== null && parseFloat(btn.dataset.start) === activeStart);
  });
}

// Called after a chapter's start/end/type is edited in place (no full
// renderChapterizeResults(), so the <video> element and its playback
// position stay untouched) to keep an open preview's chapter buttons in sync.
function refreshChapterizeJumpButtons(epIndex) {
  const ref = chapterizeState.chapterButtonRefs[epIndex];
  if (ref) renderChapterizeJumpButtons(ref.buttonsEl, ref.ep, ref.video);
}

function renderChapterizeChapterRow(container, ep, epIndex, ch, chIndex) {
  const row = document.createElement("div");
  row.className = "chapter-row" + (ch.needs_review ? " needs-review" : "");

  const badge = document.createElement("span");
  badge.className = `badge ${ch.type}`;
  badge.textContent = ch.type;

  const confidenceBadge = document.createElement("span");
  confidenceBadge.className = "confidence-badge";
  if (ch.confidence != null) {
    const pct = Math.round(ch.confidence * 100);
    confidenceBadge.textContent = `${pct}%`;
    confidenceBadge.title = `Match confidence: ${pct}%`;
    if (pct < 60) confidenceBadge.classList.add("low");
  }

  const typeSelect = document.createElement("select");
  CHAPTERIZE_CHAPTER_TYPES.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = t;
    if (t === ch.type) opt.selected = true;
    typeSelect.appendChild(opt);
  });
  typeSelect.onchange = () => {
    ch.type = typeSelect.value;
    badge.className = `badge ${ch.type}`;
    refreshChapterizeJumpButtons(epIndex);
  };

  const startInput = document.createElement("input");
  startInput.type = "text";
  startInput.className = "time-input";
  startInput.value = fmtTime(ch.start);
  startInput.onchange = () => {
    const v = parseTime(startInput.value);
    if (v !== null) ch.start = v;
    refreshChapterizeJumpButtons(epIndex);
  };

  const endInput = document.createElement("input");
  endInput.type = "text";
  endInput.className = "time-input";
  endInput.value = fmtTime(ch.end);
  endInput.onchange = () => {
    const v = parseTime(endInput.value);
    if (v !== null) ch.end = v;
    refreshChapterizeJumpButtons(epIndex);
  };

  const titleInput = document.createElement("input");
  titleInput.type = "text";
  titleInput.value = ch.title || "";
  titleInput.onchange = () => (ch.title = titleInput.value);

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "danger";
  removeBtn.textContent = "Remove";
  removeBtn.onclick = () => {
    ep.chapters.splice(chIndex, 1);
    renderChapterizeResults();
  };

  row.appendChild(badge);
  row.appendChild(confidenceBadge);
  row.appendChild(typeSelect);
  row.appendChild(startInput);
  row.appendChild(endInput);
  row.appendChild(titleInput);
  row.appendChild(removeBtn);
  container.appendChild(row);

  if (ch.needs_review && (ch.type === "opening" || ch.type === "ending")) {
    const candidates = ch.type === "opening" ? ep.opening_candidates : ep.ending_candidates;
    if (candidates && candidates.length > 1) {
      container.appendChild(renderChapterizeCandidatePicker(ep, ch, candidates));
    }
  }
}

function renderChapterizeCandidatePicker(ep, ch, candidates) {
  const picker = document.createElement("div");
  picker.className = "candidate-picker";

  const label = document.createElement("div");
  label.className = "candidate-title";
  label.textContent = `${candidates.length} possible matches found here - pick the correct one:`;
  picker.appendChild(label);

  candidates.forEach((cand) => {
    const opt = document.createElement("div");
    const isChosen = cand.theme_slug === ch.theme_slug && Math.abs(cand.start - ch.start) < 0.1;
    opt.className = "candidate-option" + (isChosen ? " chosen" : "");
    opt.textContent =
      `${cand.song_title || cand.theme_slug} (${cand.theme_slug}) at ${fmtTime(cand.start)}-${fmtTime(cand.end)}` +
      ` - score ${cand.score.toFixed(2)}`;
    opt.onclick = () => {
      ch.start = cand.start;
      ch.end = cand.end;
      ch.theme_slug = cand.theme_slug;
      ch.confidence = cand.score;
      ch.needs_review = false;
      renderChapterizeResults();
    };
    picker.appendChild(opt);
  });

  return picker;
}

document.getElementById("chapterize-save-btn").addEventListener("click", saveChapterizeChapters);

async function saveChapterizeChapters() {
  const statusEl = document.getElementById("chapterize-save-status");
  const saveBtn = document.getElementById("chapterize-save-btn");
  saveBtn.disabled = true;
  statusEl.textContent = "Saving...";
  statusEl.style.color = "";
  try {
    await api(`/api/chapterize/analyze/${chapterizeState.jobId}/chapters`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ episodes: chapterizeState.resultEpisodes }),
    });
    const data = await api(`/api/chapterize/analyze/${chapterizeState.jobId}/save`, { method: "POST" });
    const failures = (data.results || []).filter((r) => !r.ok);
    if (failures.length) {
      statusEl.textContent = `Saved with ${failures.length} error(s): ${failures.map((f) => f.error).join("; ")}`;
      statusEl.style.color = "var(--warn)";
    } else {
      statusEl.textContent = "Chapters written to all mkv files.";
      statusEl.style.color = "var(--ok)";
    }
  } catch (err) {
    statusEl.textContent = `Save failed: ${err.message}`;
    statusEl.style.color = "var(--danger)";
  }
  saveBtn.disabled = false;
}

// ---- Settings: naming schema, threshold/cache-ttl, clear cache ----

async function initChapterizeSettings() {
  document.getElementById("chapterize-save-settings-btn").addEventListener("click", saveChapterizeSettingsFromForm);
  document.getElementById("chapterize-clear-cache-btn").addEventListener("click", clearChapterizeThemeCache);
  document.getElementById("chapterize-clear-preview-cache-btn").addEventListener("click", clearChapterizePreviewCache);
  await loadChapterizeSettings();
}

async function loadChapterizeSettings() {
  const form = document.getElementById("chapterize-naming-form");
  try {
    const settings = await api("/api/chapterize/settings");
    form.innerHTML = "";
    CHAPTERIZE_NAMING_TYPES.forEach((type) => {
      const label = document.createElement("label");
      label.textContent = type[0].toUpperCase() + type.slice(1);
      const input = document.createElement("input");
      input.type = "text";
      input.id = `chapterize-naming-${type}`;
      input.value = settings.naming_schema[type] ?? "";
      form.appendChild(label);
      form.appendChild(input);
    });
    document.getElementById("chapterize-setting-threshold").value = settings.match_threshold;
    document.getElementById("chapterize-setting-cache-ttl").value = settings.animethemes_cache_ttl_days;
  } catch (err) {
    form.innerHTML = `<p class="item-sub">Failed to load: ${escapeHtml(err.message)}</p>`;
  }
}

async function saveChapterizeSettingsFromForm() {
  const naming_schema = {};
  CHAPTERIZE_NAMING_TYPES.forEach((type) => {
    naming_schema[type] = document.getElementById(`chapterize-naming-${type}`).value || type;
  });
  const payload = {
    naming_schema,
    match_threshold: parseFloat(document.getElementById("chapterize-setting-threshold").value) || 0.8,
    animethemes_cache_ttl_days: parseInt(document.getElementById("chapterize-setting-cache-ttl").value, 10) || 30,
  };
  try {
    await api("/api/chapterize/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast("Chapter analyzer settings saved", "ok");
  } catch (err) {
    toast(`Failed to save: ${err.message}`, "error");
  }
}

async function clearChapterizeThemeCache() {
  if (!confirm("Delete all cached animethemes.moe theme audio? It will be re-downloaded next time it's needed.")) return;
  const btn = document.getElementById("chapterize-clear-cache-btn");
  btn.disabled = true;
  try {
    const data = await api("/api/chapterize/animethemes/cache", { method: "DELETE" });
    toast(`Cleared ${data.removed} cached file(s)`, "ok");
  } catch (err) {
    toast(`Failed to clear cache: ${err.message}`, "error");
  }
  btn.disabled = false;
}

async function clearChapterizePreviewCache() {
  if (!confirm("Delete all cached episode preview videos? They will be rebuilt the next time you review an episode.")) return;
  const btn = document.getElementById("chapterize-clear-preview-cache-btn");
  btn.disabled = true;
  try {
    const data = await api("/api/chapterize/analyze/preview-cache", { method: "DELETE" });
    toast(`Cleared ${data.removed} cached preview(s)`, "ok");
  } catch (err) {
    toast(`Failed to clear preview cache: ${err.message}`, "error");
  }
  btn.disabled = false;
}

initChapterizeSettings();
resumeChapterizeJob();

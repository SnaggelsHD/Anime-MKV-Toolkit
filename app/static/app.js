const state = {
  libraries: [],
  selectedLibraryId: null,
  shows: [],
  expandedShowIds: new Set(),
  episodes: [],
};

function getStoredTheme() {
  try {
    return localStorage.getItem("theme");
  } catch (_) {
    return null;
  }
}

function isDarkActive() {
  const stored = getStoredTheme();
  if (stored === "dark") return true;
  if (stored === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyTheme(theme) {
  if (theme === "dark" || theme === "light") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

function initTheme() {
  applyTheme(getStoredTheme());
  const toggle = document.getElementById("theme-toggle");
  toggle.checked = isDarkActive();
  toggle.addEventListener("change", () => {
    const next = toggle.checked ? "dark" : "light";
    try {
      localStorage.setItem("theme", next);
    } catch (_) {}
    applyTheme(next);
  });
}

function toast(message, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function summarizeResults(results) {
  const ok = results.filter((r) => r.ok).length;
  const failed = results.length - ok;
  if (failed === 0) return `${ok} episode(s) succeeded`;
  return `${ok} succeeded, ${failed} failed`;
}

async function loadLibraries() {
  const list = document.getElementById("library-list");
  list.textContent = "Loading...";
  try {
    state.libraries = await api("/api/libraries");
  } catch (err) {
    list.textContent = "Failed to load libraries.";
    toast(`Failed to load libraries: ${err.message}`, "error");
    return;
  }
  renderLibraries();
}

function renderLibraries() {
  const list = document.getElementById("library-list");
  list.innerHTML = "";
  if (state.libraries.length === 0) {
    list.innerHTML = '<p id="show-detail-placeholder">No libraries found under the mounted /libraries path.</p>';
    return;
  }
  for (const lib of state.libraries) {
    const div = document.createElement("div");
    div.className = `library-item${lib.id === state.selectedLibraryId ? " selected" : ""}`;
    div.innerHTML = `
      <div class="item-row" data-role="select-library">
        <div class="item-name-wrap">
          <span class="chevron">▸</span>
          <div>
            <div class="item-name">${escapeHtml(lib.name)}</div>
            <div class="item-sub">${escapeHtml(lib.path)}</div>
            <div class="item-sub">${lib.show_count} show(s)</div>
          </div>
        </div>
      </div>
      <div class="item-actions" style="margin-top:0.4rem;">
        <button class="primary" data-action="backup-library">Backup all shows</button>
        <button data-action="restore-library">Restore all shows</button>
      </div>
    `;
    div.querySelector('[data-role="select-library"]').addEventListener("click", () => selectLibrary(lib.id));
    div.querySelector('[data-action="backup-library"]').addEventListener("click", (e) => {
      e.stopPropagation();
      runOperation(`/api/libraries/${lib.id}/backup`, `Backing up library "${lib.name}"...`);
    });
    div.querySelector('[data-action="restore-library"]').addEventListener("click", (e) => {
      e.stopPropagation();
      runOperation(`/api/libraries/${lib.id}/restore`, `Restoring library "${lib.name}"...`);
    });
    list.appendChild(div);
  }
}

async function selectLibrary(libraryId) {
  if (state.selectedLibraryId !== libraryId) {
    state.expandedShowIds.clear();
  }
  state.selectedLibraryId = libraryId;
  renderLibraries();
  const detail = document.getElementById("show-detail");
  detail.innerHTML = '<p id="show-detail-placeholder">Loading shows...</p>';
  try {
    state.shows = await api(`/api/libraries/${libraryId}/shows`);
  } catch (err) {
    detail.innerHTML = `<p id="show-detail-placeholder">Failed to load shows: ${escapeHtml(err.message)}</p>`;
    return;
  }
  renderShowDetail();
  for (const showId of Array.from(state.expandedShowIds)) {
    if (state.shows.some((s) => s.id === showId)) {
      toggleShowEpisodes(showId, true);
    }
  }
}

function renderShowDetail() {
  const detail = document.getElementById("show-detail");
  const lib = state.libraries.find((l) => l.id === state.selectedLibraryId);
  if (!lib) return;

  const showsHtml = state.shows
    .map(
      (show) => `
      <div class="show-item${state.expandedShowIds.has(show.id) ? " expanded" : ""}" data-show-item="${show.id}">
        <div class="item-row" data-show-id="${show.id}">
          <div class="item-name-wrap">
            <span class="chevron">▸</span>
            <div>
              <div class="item-name">${escapeHtml(show.name)}</div>
              <div class="item-sub">${show.episode_count} episode(s)</div>
            </div>
          </div>
          <div class="item-actions">
            <button class="primary" data-action="backup-show" data-show-id="${show.id}">Backup</button>
            <button data-action="restore-show" data-show-id="${show.id}">Restore</button>
          </div>
        </div>
        <div class="episodes-container" id="episodes-${show.id}"></div>
      </div>`
    )
    .join("");

  detail.innerHTML = `
    <h2>${escapeHtml(lib.name)} — Shows</h2>
    ${showsHtml || '<p id="show-detail-placeholder">No shows found in this library.</p>'}
  `;

  detail.querySelectorAll('[data-show-id]').forEach((el) => {
    if (el.classList.contains("item-row")) {
      el.addEventListener("click", () => toggleShowEpisodes(Number(el.dataset.showId)));
    }
  });
  detail.querySelectorAll('[data-action="backup-show"]').forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.showId);
      const show = state.shows.find((s) => s.id === id);
      runOperation(`/api/shows/${id}/backup`, `Backing up "${show.name}"...`, () => toggleShowEpisodes(id, true));
    })
  );
  detail.querySelectorAll('[data-action="restore-show"]').forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.showId);
      const show = state.shows.find((s) => s.id === id);
      runOperation(`/api/shows/${id}/restore`, `Restoring "${show.name}"...`);
    })
  );
}

async function toggleShowEpisodes(showId, forceReload = false) {
  const container = document.getElementById(`episodes-${showId}`);
  if (!container) return;
  const showItemEl = document.querySelector(`[data-show-item="${showId}"]`);

  if (state.expandedShowIds.has(showId) && !forceReload) {
    state.expandedShowIds.delete(showId);
    showItemEl?.classList.remove("expanded");
    container.innerHTML = "";
    return;
  }
  state.expandedShowIds.add(showId);
  showItemEl?.classList.add("expanded");
  container.innerHTML = '<p id="show-detail-placeholder">Loading episodes...</p>';
  try {
    state.episodes = await api(`/api/shows/${showId}/episodes`);
  } catch (err) {
    container.innerHTML = `<p id="show-detail-placeholder">Failed to load episodes: ${escapeHtml(err.message)}</p>`;
    return;
  }
  renderEpisodes(showId, container);
}

function renderEpisodes(showId, container) {
  if (state.episodes.length === 0) {
    container.innerHTML = '<p id="show-detail-placeholder">No episodes found.</p>';
    return;
  }

  const bySeason = new Map();
  for (const ep of state.episodes) {
    const key = ep.season || "Unsorted";
    if (!bySeason.has(key)) bySeason.set(key, []);
    bySeason.get(key).push(ep);
  }

  let html = "";
  for (const [season, eps] of bySeason) {
    html += `<div class="season-heading">${season === "Unsorted" ? "Unsorted" : `Season ${escapeHtml(season)}`}</div>`;
    for (const ep of eps) {
      html += `
        <div class="episode-row" data-episode-id="${ep.id}">
          <div class="episode-name">${escapeHtml(ep.filename)}</div>
          <div class="flags">
            <span class="${ep.has_chapters ? "flag-ok" : "flag-missing"}">${ep.has_chapters ? "chapters ✓" : "chapters ✗"}</span>
            &nbsp;
            <span class="${ep.has_track_metadata ? "flag-ok" : "flag-missing"}">${ep.has_track_metadata ? "tracks ✓" : "tracks ✗"}</span>
          </div>
        </div>`;
    }
  }
  container.innerHTML = html;
  container.querySelectorAll("[data-episode-id]").forEach((el) =>
    el.addEventListener("click", () => openEpisodeDetail(Number(el.dataset.episodeId)))
  );
}

function parseChapterAtoms(xmlString) {
  try {
    const doc = new DOMParser().parseFromString(xmlString, "application/xml");
    if (doc.querySelector("parsererror")) return [];
    const atoms = Array.from(doc.getElementsByTagName("ChapterAtom"));
    return atoms.map((atom, i) => {
      const start = atom.getElementsByTagName("ChapterTimeStart")[0]?.textContent || "";
      const end = atom.getElementsByTagName("ChapterTimeEnd")[0]?.textContent || "";
      const display = atom.getElementsByTagName("ChapterDisplay")[0];
      const title = display ? display.getElementsByTagName("ChapterString")[0]?.textContent || "" : "";
      return { index: i + 1, title, start, end };
    });
  } catch (_) {
    return [];
  }
}

function formatChapterTime(t) {
  if (!t) return "";
  const [hms, frac] = t.split(".");
  return frac ? `${hms}.${frac.slice(0, 3)}` : hms;
}

async function openEpisodeDetail(episodeId) {
  const modalRoot = document.getElementById("modal-root");
  modalRoot.innerHTML = '<div class="modal-backdrop"><div class="modal">Loading...</div></div>';
  let ep;
  try {
    ep = await api(`/api/episodes/${episodeId}`);
  } catch (err) {
    toast(`Failed to load episode: ${err.message}`, "error");
    modalRoot.innerHTML = "";
    return;
  }

  let tracksHtml = '<p class="item-sub">No track metadata stored.</p>';
  if (ep.track_metadata) {
    try {
      const tracks = JSON.parse(ep.track_metadata);
      tracksHtml = `
        <table>
          <thead><tr><th>ID</th><th>Type</th><th>Lang</th><th>Name</th><th>Default</th><th>Forced</th><th>Codec</th></tr></thead>
          <tbody>
            ${tracks
              .map(
                (t) => `<tr>
                  <td>${t.track_id ?? ""}</td>
                  <td>${escapeHtml(t.track_type || "")}</td>
                  <td>${escapeHtml(t.language || "")}</td>
                  <td>${escapeHtml(t.name || "")}</td>
                  <td>${t.default ? "yes" : "no"}</td>
                  <td>${t.forced ? "yes" : "no"}</td>
                  <td>${escapeHtml(t.codec || "")}</td>
                </tr>`
              )
              .join("")}
          </tbody>
        </table>`;
    } catch (_) {
      tracksHtml = "<p class=\"item-sub\">Could not parse stored track metadata.</p>";
    }
  }

  let chaptersSectionHtml;
  if (ep.chapters) {
    const chapters = parseChapterAtoms(ep.chapters);
    const tableRows = chapters.length
      ? chapters
          .map(
            (c) => `<tr>
              <td>${c.index}</td>
              <td>${escapeHtml(c.title)}</td>
              <td>${escapeHtml(formatChapterTime(c.start))}</td>
              <td>${escapeHtml(formatChapterTime(c.end))}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="4" class="item-sub">Could not parse chapters as a table.</td></tr>`;

    chaptersSectionHtml = `
      <div class="section-header">
        <h2 style="margin-top:0;">Chapters</h2>
        <div class="view-toggle">
          <button type="button" class="active" data-chapters-toggle="xml">XML</button>
          <button type="button" data-chapters-toggle="table">Table</button>
        </div>
      </div>
      <div data-chapters-view="xml"><pre>${escapeHtml(ep.chapters)}</pre></div>
      <div data-chapters-view="table" style="display:none;">
        <table>
          <thead><tr><th>#</th><th>Title</th><th>Start</th><th>End</th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>
    `;
  } else {
    chaptersSectionHtml = '<h2>Chapters</h2><p class="item-sub">No chapters stored.</p>';
  }

  modalRoot.innerHTML = `
    <div class="modal-backdrop" id="modal-backdrop">
      <div class="modal">
        <div class="modal-header">
          <h3>${escapeHtml(ep.filename)}</h3>
          <button data-action="close-modal">Close</button>
        </div>
        <p class="item-sub">${escapeHtml(ep.path)}</p>
        <div class="item-actions" style="margin: 0.5rem 0 1rem 0;">
          <button class="primary" data-action="backup-episode">Backup this episode</button>
          <button data-action="restore-episode">Restore chapters</button>
        </div>
        ${chaptersSectionHtml}
        <h2 style="margin-top:1rem;">Track Metadata</h2>
        ${tracksHtml}
      </div>
    </div>
  `;

  document.getElementById("modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "modal-backdrop") closeModal();
  });
  modalRoot.querySelectorAll('[data-chapters-toggle]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.chaptersToggle;
      modalRoot.querySelectorAll('[data-chapters-toggle]').forEach((b) => b.classList.toggle("active", b === btn));
      modalRoot.querySelectorAll('[data-chapters-view]').forEach((el) => {
        el.style.display = el.dataset.chaptersView === view ? "" : "none";
      });
    });
  });
  modalRoot.querySelector('[data-action="close-modal"]').addEventListener("click", closeModal);
  modalRoot.querySelector('[data-action="backup-episode"]').addEventListener("click", async () => {
    await runSingle(`/api/episodes/${episodeId}/backup`, "Backing up episode...");
    openEpisodeDetail(episodeId);
    if (state.expandedShowIds.has(ep.show_id)) toggleShowEpisodes(ep.show_id, true);
  });
  modalRoot.querySelector('[data-action="restore-episode"]').addEventListener("click", async () => {
    await runSingle(`/api/episodes/${episodeId}/restore`, "Restoring chapters...");
  });
}

function closeModal() {
  document.getElementById("modal-root").innerHTML = "";
}

async function runSingle(path, pendingMessage) {
  setStatus(pendingMessage);
  try {
    const result = await api(path, { method: "POST" });
    if (result.ok) {
      toast(`${result.filename}: success`, "ok");
    } else {
      toast(`${result.filename}: ${result.error}`, "error");
    }
  } catch (err) {
    toast(`Operation failed: ${err.message}`, "error");
  } finally {
    setStatus("");
  }
}

async function runOperation(path, pendingMessage, onDone) {
  setStatus(pendingMessage);
  try {
    const { results } = await api(path, { method: "POST" });
    toast(summarizeResults(results), results.every((r) => r.ok) ? "ok" : "error");
    if (state.selectedLibraryId) await selectLibrary(state.selectedLibraryId);
    if (onDone) onDone();
  } catch (err) {
    toast(`Operation failed: ${err.message}`, "error");
  } finally {
    setStatus("");
  }
}

function initTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.toggle("active", b === btn));
      document.getElementById("view-library").style.display = btn.dataset.tab === "library" ? "" : "none";
      document.getElementById("view-settings").style.display = btn.dataset.tab === "settings" ? "" : "none";
      if (btn.dataset.tab === "settings") populateClearLibraries();
    });
  });
}

const UNSORTED_SEASON = "__unsorted__";
let clearFormEpisodes = [];

function resetSelect(select, placeholder, disabled) {
  select.innerHTML = `<option value="">${placeholder}</option>`;
  select.disabled = disabled;
}

async function populateClearLibraries() {
  const libSelect = document.getElementById("clear-library");
  const showSelect = document.getElementById("clear-show");
  const seasonSelect = document.getElementById("clear-season");
  const episodeSelect = document.getElementById("clear-episode");
  const clearBtn = document.getElementById("clear-selected-btn");

  resetSelect(showSelect, "Select a show…", true);
  resetSelect(seasonSelect, "All seasons", true);
  resetSelect(episodeSelect, "All episodes", true);
  clearBtn.disabled = true;

  const previousValue = libSelect.value;
  libSelect.innerHTML = '<option value="">Select a library…</option>';
  try {
    const libraries = await api("/api/libraries");
    for (const lib of libraries) {
      const opt = document.createElement("option");
      opt.value = lib.id;
      opt.textContent = `${lib.name} (${lib.show_count} shows)`;
      libSelect.appendChild(opt);
    }
    if (previousValue && libraries.some((l) => String(l.id) === previousValue)) {
      libSelect.value = previousValue;
    }
  } catch (err) {
    toast(`Failed to load libraries: ${err.message}`, "error");
  }
}

function populateClearEpisodeOptions(seasonValue) {
  const episodeSelect = document.getElementById("clear-episode");
  let filtered;
  if (seasonValue === "") filtered = clearFormEpisodes;
  else if (seasonValue === UNSORTED_SEASON) filtered = clearFormEpisodes.filter((e) => !e.season);
  else filtered = clearFormEpisodes.filter((e) => e.season === seasonValue);

  episodeSelect.innerHTML = '<option value="">All episodes</option>';
  for (const ep of filtered) {
    const opt = document.createElement("option");
    opt.value = ep.id;
    opt.textContent = ep.filename;
    episodeSelect.appendChild(opt);
  }
  episodeSelect.disabled = false;
}

function initSettingsTab() {
  const libSelect = document.getElementById("clear-library");
  const showSelect = document.getElementById("clear-show");
  const seasonSelect = document.getElementById("clear-season");
  const episodeSelect = document.getElementById("clear-episode");
  const clearBtn = document.getElementById("clear-selected-btn");

  document.getElementById("settings-backup-all").addEventListener("click", () => {
    runOperation("/api/backup/all", "Backing up all libraries...", loadLibraries);
  });
  document.getElementById("settings-restore-all").addEventListener("click", () => {
    runOperation("/api/restore/all", "Restoring all libraries...", loadLibraries);
  });

  document.getElementById("settings-clear-db").addEventListener("click", async () => {
    if (
      !confirm(
        "Clear the ENTIRE database? This permanently deletes every stored chapter and track metadata record for every library. Files on disk are never touched. This cannot be undone."
      )
    ) {
      return;
    }
    try {
      await api("/api/database", { method: "DELETE" });
      toast("Database cleared", "ok");
      loadLibraries();
      if (state.selectedLibraryId) selectLibrary(state.selectedLibraryId);
      populateClearLibraries();
    } catch (err) {
      toast(`Failed to clear database: ${err.message}`, "error");
    }
  });

  libSelect.addEventListener("change", async () => {
    resetSelect(showSelect, "Select a show…", true);
    resetSelect(seasonSelect, "All seasons", true);
    resetSelect(episodeSelect, "All episodes", true);
    clearFormEpisodes = [];
    clearBtn.disabled = true;
    if (!libSelect.value) return;
    try {
      const shows = await api(`/api/libraries/${libSelect.value}/shows`);
      showSelect.innerHTML = '<option value="">Select a show…</option>';
      for (const show of shows) {
        const opt = document.createElement("option");
        opt.value = show.id;
        opt.textContent = `${show.name} (${show.episode_count} eps)`;
        showSelect.appendChild(opt);
      }
      showSelect.disabled = false;
    } catch (err) {
      toast(`Failed to load shows: ${err.message}`, "error");
    }
  });

  showSelect.addEventListener("change", async () => {
    resetSelect(seasonSelect, "All seasons", true);
    resetSelect(episodeSelect, "All episodes", true);
    clearFormEpisodes = [];
    if (!showSelect.value) {
      clearBtn.disabled = true;
      return;
    }
    clearBtn.disabled = false;
    try {
      clearFormEpisodes = await api(`/api/shows/${showSelect.value}/episodes`);
      const seasons = Array.from(new Set(clearFormEpisodes.map((e) => e.season || UNSORTED_SEASON)));
      seasonSelect.innerHTML = '<option value="">All seasons</option>';
      for (const season of seasons) {
        const opt = document.createElement("option");
        opt.value = season;
        opt.textContent = season === UNSORTED_SEASON ? "Unsorted" : `Season ${season}`;
        seasonSelect.appendChild(opt);
      }
      seasonSelect.disabled = false;
      populateClearEpisodeOptions("");
    } catch (err) {
      toast(`Failed to load episodes: ${err.message}`, "error");
    }
  });

  seasonSelect.addEventListener("change", () => {
    populateClearEpisodeOptions(seasonSelect.value);
  });

  clearBtn.addEventListener("click", async () => {
    const showId = showSelect.value;
    const showName = showSelect.options[showSelect.selectedIndex]?.textContent || "this show";
    const seasonValue = seasonSelect.value;
    const episodeId = episodeSelect.value;
    const episodeName = episodeSelect.options[episodeSelect.selectedIndex]?.textContent || "";

    let scopeLabel;
    let request;
    if (episodeId) {
      scopeLabel = `episode "${episodeName}"`;
      request = () => api(`/api/episodes/${episodeId}`, { method: "DELETE" });
    } else if (seasonValue === UNSORTED_SEASON) {
      scopeLabel = `unsorted episodes of "${showName}"`;
      request = () => api(`/api/shows/${showId}/season`, { method: "DELETE" });
    } else if (seasonValue !== "") {
      scopeLabel = `Season ${seasonValue} of "${showName}"`;
      request = () => api(`/api/shows/${showId}/season?season=${encodeURIComponent(seasonValue)}`, { method: "DELETE" });
    } else {
      scopeLabel = `show "${showName}"`;
      request = () => api(`/api/shows/${showId}`, { method: "DELETE" });
    }

    if (!confirm(`Clear ${scopeLabel} from the database? This cannot be undone. Files on disk are never touched.`)) {
      return;
    }
    try {
      await request();
      toast(`Cleared ${scopeLabel}`, "ok");
      if (state.selectedLibraryId) selectLibrary(state.selectedLibraryId);
      libSelect.dispatchEvent(new Event("change"));
    } catch (err) {
      toast(`Failed to clear: ${err.message}`, "error");
    }
  });
}

function setStatus(text) {
  document.getElementById("status-line").textContent = text;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

initTheme();
initTabs();
initSettingsTab();
loadLibraries();

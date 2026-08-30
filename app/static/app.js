const state = {
  libraries: [],
  selectedLibraryId: null,
  shows: [],
  selectedShowId: null,
  episodes: [],
};

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
    div.className = "library-item";
    div.innerHTML = `
      <div class="item-row" data-role="select-library">
        <div>
          <div class="item-name">${escapeHtml(lib.name)}</div>
          <div class="item-sub">${escapeHtml(lib.path)}</div>
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
  state.selectedLibraryId = libraryId;
  state.selectedShowId = null;
  const detail = document.getElementById("show-detail");
  detail.innerHTML = '<p id="show-detail-placeholder">Loading shows...</p>';
  try {
    state.shows = await api(`/api/libraries/${libraryId}/shows`);
  } catch (err) {
    detail.innerHTML = `<p id="show-detail-placeholder">Failed to load shows: ${escapeHtml(err.message)}</p>`;
    return;
  }
  renderShowDetail();
}

function renderShowDetail() {
  const detail = document.getElementById("show-detail");
  const lib = state.libraries.find((l) => l.id === state.selectedLibraryId);
  if (!lib) return;

  const showsHtml = state.shows
    .map(
      (show) => `
      <div class="show-item">
        <div class="item-row" data-show-id="${show.id}">
          <div>
            <div class="item-name">${escapeHtml(show.name)}</div>
            <div class="item-sub">${show.episode_count} episode(s)</div>
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
  if (state.selectedShowId === showId && !forceReload) {
    state.selectedShowId = null;
    container.innerHTML = "";
    return;
  }
  state.selectedShowId = showId;
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

  const chaptersHtml = ep.chapters
    ? `<pre>${escapeHtml(ep.chapters)}</pre>`
    : '<p class="item-sub">No chapters stored.</p>';

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
        <h2>Chapters</h2>
        ${chaptersHtml}
        <h2 style="margin-top:1rem;">Track Metadata</h2>
        ${tracksHtml}
      </div>
    </div>
  `;

  document.getElementById("modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "modal-backdrop") closeModal();
  });
  modalRoot.querySelector('[data-action="close-modal"]').addEventListener("click", closeModal);
  modalRoot.querySelector('[data-action="backup-episode"]').addEventListener("click", async () => {
    await runSingle(`/api/episodes/${episodeId}/backup`, "Backing up episode...");
    openEpisodeDetail(episodeId);
    if (state.selectedShowId) toggleShowEpisodes(state.selectedShowId, true);
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

loadLibraries();

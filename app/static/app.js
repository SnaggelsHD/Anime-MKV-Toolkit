const state = {
  libraries: [],
  selectedLibraryId: null,
  shows: [],
  expandedShowIds: new Set(),
  episodes: [],
  detailKind: null, // "show" | "episode" | null
  detailId: null,
};

const UNSORTED_SEASON = "__unsorted__";

function seasonQuery(seasonKey) {
  return seasonKey === UNSORTED_SEASON ? "" : `?season=${encodeURIComponent(seasonKey)}`;
}

function withDryRun(url) {
  return url + (url.includes("?") ? "&" : "?") + "dry_run=true";
}

const POSTER_PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 150">' +
      '<rect width="100" height="150" rx="6" fill="#2a2e37"/>' +
      '<circle cx="38" cy="45" r="10" fill="#4b5262"/>' +
      '<path d="M22 100 L42 72 L58 92 L72 68 L86 100 Z" fill="#4b5262"/>' +
      "</svg>"
  );

// Sets each poster <img data-poster-src="..."> tag's src, falling back to a
// placeholder icon either immediately (no data-poster-src, e.g. an unsorted
// season with no folder to look for a poster in) or if the request 404s
// (no poster file present in that show/season folder).
function wirePosterImages(root) {
  root.querySelectorAll("img.poster-thumb").forEach((img) => {
    const src = img.dataset.posterSrc;
    if (!src) {
      img.src = POSTER_PLACEHOLDER;
      return;
    }
    img.addEventListener("error", () => {
      img.onerror = null;
      img.src = POSTER_PLACEHOLDER;
    });
    img.src = src;
  });
}

function closeAllMenus() {
  document.querySelectorAll(".menu-dropdown").forEach((el) => {
    el.hidden = true;
  });
}
document.addEventListener("click", () => closeAllMenus());

// Wires each "..." menu button found under `root` to show/hide its dropdown.
// The dropdown's own click listener runs in the capture phase so it can close
// the menu before the click reaches an individual action button - but it must
// NOT call stopPropagation() for a button click: stopPropagation() during the
// capture phase stops dispatch from ever reaching the target at all, which
// would silently kill every action button's own click handler. It only stops
// propagation for a click on the dropdown's own blank space, so that doesn't
// bubble up and trigger the row's select/expand handler.
function wireMenus(root) {
  root.querySelectorAll(".menu-wrap").forEach((wrap) => {
    const toggle = wrap.querySelector('[data-action="toggle-menu"]');
    const dropdown = wrap.querySelector(".menu-dropdown");
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const wasHidden = dropdown.hidden;
      closeAllMenus();
      dropdown.hidden = !wasHidden;
    });
    dropdown.addEventListener(
      "click",
      (e) => {
        if (e.target.closest("button")) {
          dropdown.hidden = true;
        } else {
          e.stopPropagation();
        }
      },
      true
    );
  });
}

function formatTimestamp(iso) {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

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

const tasks = new Map();

function renderTaskCard(job) {
  const pct = job.total > 0 ? Math.min(100, Math.round((job.completed / job.total) * 100)) : 100;
  let cardClass = "task-card";
  let statusText;

  if (job.status === "running") {
    statusText = `${job.completed} / ${job.total}`;
  } else if (job.status === "error") {
    cardClass += " error";
    statusText = `Error: ${job.error}`;
  } else {
    const ok = job.results.filter((r) => r.ok).length;
    const failed = job.results.length - ok;
    statusText = failed === 0 ? `${ok} succeeded` : `${ok} succeeded, ${failed} failed`;
    if (failed > 0) cardClass += " has-failures";
  }

  return `
    <div class="${cardClass}" data-job-id="${job.id}">
      <div class="task-card-header">
        <span class="task-card-label" title="${escapeHtml(job.label)}">${escapeHtml(job.label)}</span>
        <button type="button" class="task-card-close" data-close-job="${job.id}" aria-label="Dismiss">×</button>
      </div>
      <div class="task-progress-track"><div class="task-progress-fill" style="width:${pct}%"></div></div>
      <div class="task-card-status">${escapeHtml(statusText)}</div>
    </div>
  `;
}

function renderTaskQueue() {
  const container = document.getElementById("task-queue");
  container.innerHTML = Array.from(tasks.values())
    .map((entry) => renderTaskCard(entry.job))
    .join("");
  container.querySelectorAll("[data-close-job]").forEach((btn) =>
    btn.addEventListener("click", () => removeTask(btn.dataset.closeJob))
  );
}

function removeTask(jobId) {
  const entry = tasks.get(jobId);
  if (entry?.intervalId) clearInterval(entry.intervalId);
  tasks.delete(jobId);
  renderTaskQueue();
}

function trackJob(jobId, onDone) {
  const entry = { job: null, intervalId: null, onDone };
  tasks.set(jobId, entry);

  const poll = async () => {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}`);
    } catch (_) {
      clearInterval(entry.intervalId);
      tasks.delete(jobId);
      return;
    }
    entry.job = job;
    renderTaskQueue();
    if (job.status !== "running") {
      clearInterval(entry.intervalId);
      if (entry.onDone) entry.onDone(job);
      setTimeout(() => removeTask(jobId), 8000);
    }
  };

  entry.intervalId = setInterval(poll, 600);
  poll();
}

async function startJob(path, onDone) {
  let jobId;
  try {
    const res = await api(path, { method: "POST" });
    jobId = res.job_id;
  } catch (err) {
    toast(`Failed to start operation: ${err.message}`, "error");
    return;
  }
  trackJob(jobId, onDone);
}

async function resumeActiveJobs() {
  let jobs;
  try {
    jobs = await api("/api/jobs");
  } catch (_) {
    return;
  }
  for (const job of jobs) {
    if (job.status === "running") trackJob(job.id);
  }
}

async function loadLibraries() {
  const select = document.getElementById("library-select");
  try {
    state.libraries = await api("/api/libraries");
  } catch (err) {
    toast(`Failed to load libraries: ${err.message}`, "error");
    return;
  }
  if (state.libraries.length === 0) {
    select.innerHTML = '<option value="">No libraries found</option>';
    document.getElementById("shows-list").innerHTML = "";
    document.getElementById("library-summary").innerHTML =
      '<p class="placeholder-text">No libraries found under the mounted /libraries path.</p>';
    resetDetailPanel();
    return;
  }
  const stillExists = state.libraries.some((l) => l.id === state.selectedLibraryId);
  await selectLibrary(stillExists ? state.selectedLibraryId : state.libraries[0].id);
}

function renderLibrarySelect() {
  const select = document.getElementById("library-select");
  select.innerHTML = state.libraries
    .map(
      (lib) =>
        `<option value="${lib.id}" ${lib.id === state.selectedLibraryId ? "selected" : ""}>${escapeHtml(lib.name)}${lib.missing ? " (missing)" : ""}</option>`
    )
    .join("");
}

function renderLibrarySummary() {
  const container = document.getElementById("library-summary");
  const lib = state.libraries.find((l) => l.id === state.selectedLibraryId);
  if (!lib) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = `
    <div class="library-summary-row">
      <div class="item-name-wrap">
        <div>
          <div class="item-name">${escapeHtml(lib.name)}${lib.missing ? ' <span class="badge-missing">MISSING</span>' : ""}</div>
          <div class="item-sub">${escapeHtml(lib.path)}</div>
          <div class="item-sub">${lib.show_count} show(s) • ${lib.cleaned_count} cleaned</div>
        </div>
      </div>
      <div class="item-actions">
        <div class="menu-wrap">
          <button type="button" class="menu-toggle" data-action="toggle-menu" aria-label="Actions" title="Actions">☰</button>
          <div class="menu-dropdown" hidden>
            <button data-action="scan-library">Scan Library</button>
            <button class="primary" data-action="backup-library">${lib.backed_up_count > 0 ? "Re-backup Library" : "Backup Library"}</button>
            <button data-action="clean-library">${lib.cleaned_count > 0 ? "Re-clean Library" : "Clean Library"}</button>
            <button data-action="dryrun-library">Dry Run Library</button>
          </div>
        </div>
      </div>
    </div>
  `;
  container.querySelector('[data-action="scan-library"]').addEventListener("click", () => {
    if (!confirm(`Scan library "${lib.name}"? This re-extracts chapters and mediainfo from every episode on disk and may take a while.`)) {
      return;
    }
    startJob(`/api/libraries/${lib.id}/scan`, () => selectLibrary(lib.id));
  });
  container.querySelector('[data-action="backup-library"]').addEventListener("click", () => {
    if (
      lib.backed_up_count > 0 &&
      !confirm(`Re-backup all shows in "${lib.name}"? This overwrites the existing backup with the current scan data.`)
    ) {
      return;
    }
    startJob(`/api/libraries/${lib.id}/backup`, () => selectLibrary(lib.id));
  });
  container.querySelector('[data-action="clean-library"]').addEventListener("click", () => {
    if (
      lib.cleaned_count > 0 &&
      !confirm(`Re-clean up all shows in "${lib.name}"? This rewrites track languages/names and container metadata in every MKV file in this library.`)
    ) {
      return;
    }
    startJob(`/api/cleanup/libraries/${lib.id}/clean`, () => selectLibrary(lib.id));
  });
  container.querySelector('[data-action="dryrun-library"]').addEventListener("click", () => {
    startJob(withDryRun(`/api/cleanup/libraries/${lib.id}/clean`), (job) => openDryRunResults(job));
  });
  wireMenus(container);
}

async function selectLibrary(libraryId) {
  const changingLibrary = state.selectedLibraryId !== libraryId;
  if (changingLibrary) {
    state.expandedShowIds.clear();
    resetDetailPanel();
  }
  state.selectedLibraryId = libraryId;
  renderLibrarySelect();
  renderLibrarySummary();
  const list = document.getElementById("shows-list");
  list.innerHTML = '<p class="placeholder-text">Loading shows...</p>';
  try {
    state.shows = await api(`/api/libraries/${libraryId}/shows`);
  } catch (err) {
    list.innerHTML = `<p class="placeholder-text">Failed to load shows: ${escapeHtml(err.message)}</p>`;
    return;
  }
  renderShowsList();
  for (const showId of Array.from(state.expandedShowIds)) {
    if (state.shows.some((s) => s.id === showId)) {
      toggleShowEpisodes(showId, true);
    }
  }
  if (state.detailKind === "show" && !changingLibrary) {
    renderShowOverview(state.detailId);
  }
}

function afterShowMutation(showId) {
  // selectLibrary() re-renders the current show overview itself (see its
  // trailing check) if this show is what's showing in the detail panel.
  return selectLibrary(state.selectedLibraryId).then(() => toggleShowEpisodes(showId, true));
}

function renderShowsList() {
  const list = document.getElementById("shows-list");

  // Show rows carry no action buttons of their own - clicking a row always
  // opens the show overview in the detail panel, which has the full action
  // set. Keeping them only there avoids showing every action twice.
  const showsHtml = state.shows
    .map(
      (show) => `
      <div class="show-item${state.expandedShowIds.has(show.id) ? " expanded" : ""}${state.detailKind === "show" && state.detailId === show.id ? " selected" : ""}" data-show-item="${show.id}">
        <div class="item-row" data-show-id="${show.id}">
          <div class="item-name-wrap">
            <span class="chevron">▸</span>
            <img class="poster-thumb" data-poster-src="/api/shows/${show.id}/poster" alt="">
            <div>
              <div class="item-name">${escapeHtml(show.name)}${show.missing ? ' <span class="badge-missing">MISSING</span>' : ""}</div>
              <div class="item-sub">${show.episode_count} episode(s) • ${show.scanned_count} scanned • ${show.backed_up_count} backed up • ${show.cleaned_count} cleaned</div>
            </div>
          </div>
        </div>
        <div class="episodes-container" id="episodes-${show.id}"></div>
      </div>`
    )
    .join("");

  list.innerHTML = showsHtml || '<p class="placeholder-text">No shows found in this library.</p>';

  wirePosterImages(list);
  list.querySelectorAll('[data-show-id]').forEach((el) => {
    el.addEventListener("click", () => {
      const id = Number(el.dataset.showId);
      toggleShowEpisodes(id);
      document.querySelectorAll(".episode-row.selected").forEach((row) => row.classList.remove("selected"));
      document.querySelectorAll(".show-item.selected").forEach((row) => row.classList.remove("selected"));
      el.closest(".show-item")?.classList.add("selected");
      renderShowOverview(id);
    });
  });
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
  container.innerHTML = '<p class="placeholder-text">Loading episodes...</p>';
  try {
    state.episodes = await api(`/api/shows/${showId}/episodes`);
  } catch (err) {
    container.innerHTML = `<p class="placeholder-text">Failed to load episodes: ${escapeHtml(err.message)}</p>`;
    return;
  }
  renderEpisodes(showId, container);
}

function renderEpisodes(showId, container) {
  if (state.episodes.length === 0) {
    container.innerHTML = '<p class="placeholder-text">No episodes found.</p>';
    return;
  }

  const bySeason = new Map();
  for (const ep of state.episodes) {
    const key = ep.season || UNSORTED_SEASON;
    if (!bySeason.has(key)) bySeason.set(key, []);
    bySeason.get(key).push(ep);
  }

  let html = "";
  for (const [seasonKey, eps] of bySeason) {
    const label = seasonKey === UNSORTED_SEASON ? "Unsorted" : `Season ${escapeHtml(seasonKey)}`;
    const scannedCount = eps.filter((e) => e.has_scan).length;
    const backedUpCount = eps.filter((e) => e.has_backup).length;
    const cleanedCount = eps.filter((e) => e.cleanup_ok).length;
    const seasonPosterAttr =
      seasonKey === UNSORTED_SEASON
        ? ""
        : `data-poster-src="/api/shows/${showId}/season-poster${seasonQuery(seasonKey)}"`;
    html += `
      <div class="season-heading-row">
        <div class="season-heading-wrap">
          <img class="poster-thumb" ${seasonPosterAttr} alt="">
          <span class="season-heading">${label} • ${eps.length} eps, ${scannedCount} scanned, ${backedUpCount} backed up, ${cleanedCount} cleaned</span>
        </div>
        <div class="item-actions">
          <button data-action="scan-season" data-season="${escapeHtml(seasonKey)}" data-scanned-count="${scannedCount}">${scannedCount > 0 ? "Rescan Season" : "Scan Season"}</button>
          <button class="primary" data-action="backup-season" data-season="${escapeHtml(seasonKey)}" data-backed-up-count="${backedUpCount}">${backedUpCount > 0 ? "Re-backup Season" : "Backup Season"}</button>
          <div class="menu-wrap">
            <button type="button" class="menu-toggle" data-action="toggle-menu" aria-label="Actions" title="Actions">☰</button>
            <div class="menu-dropdown" hidden>
              <button data-action="clean-season" data-season="${escapeHtml(seasonKey)}" data-cleaned-count="${cleanedCount}">${cleanedCount > 0 ? "Re-clean Season" : "Clean Season"}</button>
              <button data-action="dryrun-season" data-season="${escapeHtml(seasonKey)}">Dry Run Season</button>
              <button class="danger" data-action="clear-season" data-season="${escapeHtml(seasonKey)}">Clear Season</button>
            </div>
          </div>
        </div>
      </div>`;
    for (const ep of eps) {
      let cleanFlag;
      if (!ep.has_cleanup) cleanFlag = '<span class="flag-missing">cleaned ✗</span>';
      else if (ep.cleanup_ok) cleanFlag = '<span class="flag-ok">cleaned ✓</span>';
      else cleanFlag = '<span class="flag-missing">cleanup failed ✗</span>';
      const epSelected = state.detailKind === "episode" && state.detailId === ep.id;
      html += `
        <div class="episode-row${epSelected ? " selected" : ""}" data-episode-id="${ep.id}">
          <div class="episode-name">${escapeHtml(ep.filename)}${ep.missing ? ' <span class="badge-missing">MISSING</span>' : ""}</div>
          <div class="flags">
            <span class="${ep.has_scan ? "flag-ok" : "flag-missing"}">${ep.has_scan ? "scanned ✓" : "scanned ✗"}</span>
            &nbsp;
            <span class="${ep.has_backup ? "flag-ok" : "flag-missing"}">${ep.has_backup ? "backed up ✓" : "backed up ✗"}</span>
            &nbsp;
            ${cleanFlag}
          </div>
        </div>`;
    }
  }
  container.innerHTML = html;
  wirePosterImages(container);
  wireMenus(container);
  container.querySelectorAll("[data-episode-id]").forEach((el) =>
    el.addEventListener("click", () => {
      document.querySelectorAll(".episode-row.selected").forEach((row) => row.classList.remove("selected"));
      document.querySelectorAll(".show-item.selected").forEach((row) => row.classList.remove("selected"));
      el.classList.add("selected");
      renderEpisodeDetail(Number(el.dataset.episodeId));
    })
  );
  container.querySelectorAll('[data-action="scan-season"]').forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const label = btn.dataset.season === UNSORTED_SEASON ? "Unsorted" : `Season ${btn.dataset.season}`;
      if (
        Number(btn.dataset.scannedCount) > 0 &&
        !confirm(`Rescan ${label}? This re-extracts chapters and mediainfo from the files on disk, overwriting the current scan data. Backed-up data is not affected.`)
      ) {
        return;
      }
      startJob(`/api/shows/${showId}/season/scan${seasonQuery(btn.dataset.season)}`, () => toggleShowEpisodes(showId, true));
    })
  );
  container.querySelectorAll('[data-action="backup-season"]').forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const label = btn.dataset.season === UNSORTED_SEASON ? "Unsorted" : `Season ${btn.dataset.season}`;
      if (
        Number(btn.dataset.backedUpCount) > 0 &&
        !confirm(`Re-backup ${label}? This overwrites the existing backup with the current scan data.`)
      ) {
        return;
      }
      startJob(`/api/shows/${showId}/season/backup${seasonQuery(btn.dataset.season)}`, () => toggleShowEpisodes(showId, true));
    })
  );
  container.querySelectorAll('[data-action="clean-season"]').forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const label = btn.dataset.season === UNSORTED_SEASON ? "Unsorted" : `Season ${btn.dataset.season}`;
      if (
        Number(btn.dataset.cleanedCount) > 0 &&
        !confirm(`Re-clean up ${label}? This rewrites track languages/names and container metadata in every MKV file in this season.`)
      ) {
        return;
      }
      startJob(`/api/cleanup/shows/${showId}/season/clean${seasonQuery(btn.dataset.season)}`, () => toggleShowEpisodes(showId, true));
    })
  );
  container.querySelectorAll('[data-action="dryrun-season"]').forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      startJob(
        withDryRun(`/api/cleanup/shows/${showId}/season/clean${seasonQuery(btn.dataset.season)}`),
        (job) => openDryRunResults(job)
      );
    })
  );
  container.querySelectorAll('[data-action="clear-season"]').forEach((btn) =>
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const label = btn.dataset.season === UNSORTED_SEASON ? "Unsorted" : `Season ${btn.dataset.season}`;
      if (!confirm(`Clear backup data for ${label}? This cannot be undone. Files on disk and the scan database are never touched.`)) {
        return;
      }
      try {
        await api(`/api/shows/${showId}/season${seasonQuery(btn.dataset.season)}`, { method: "DELETE" });
        toast(`Cleared backup for ${label}`, "ok");
        await selectLibrary(state.selectedLibraryId);
        toggleShowEpisodes(showId, true);
      } catch (err) {
        toast(`Failed to clear: ${err.message}`, "error");
      }
    })
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

const TRACK_TYPES = ["Video", "Audio", "Text"];

function parseMediaInfoTracks(jsonString) {
  let parsed;
  try {
    parsed = JSON.parse(jsonString);
  } catch (_) {
    return { status: "invalid" };
  }

  const rawTracks = parsed?.media?.track;
  if (!rawTracks) {
    // Older backups stored a plain array from mkvmerge instead of a full mediainfo report.
    return { status: "legacy" };
  }

  const allTracks = Array.isArray(rawTracks) ? rawTracks : [rawTracks];
  const tracks = allTracks
    .filter((t) => TRACK_TYPES.includes(t["@type"]))
    .map((t) => ({
      id: t.ID ?? t.StreamOrder ?? "",
      type: t["@type"],
      language: t.Language || "",
      name: t.Title || "",
      default: t.Default === "Yes",
      forced: t.Forced === "Yes",
      format: t.Format || "",
    }));
  return { status: "ok", tracks };
}

function prettyPrintJson(jsonString) {
  try {
    return JSON.stringify(JSON.parse(jsonString), null, 2);
  } catch (_) {
    return jsonString;
  }
}

function buildToggleSection(groupId, title, tabs, defaultKey) {
  const toggleButtons = tabs
    .map(
      (t) =>
        `<button type="button" class="${t.key === defaultKey ? "active" : ""}" data-toggle-group="${groupId}" data-toggle-key="${t.key}">${escapeHtml(t.label)}</button>`
    )
    .join("");
  const panels = tabs
    .map(
      (t) =>
        `<div data-panel-group="${groupId}" data-panel-key="${t.key}" ${t.key === defaultKey ? "" : 'style="display:none;"'}>${t.html}</div>`
    )
    .join("");
  return `
    <div class="section-header">
      <h2 style="margin-top:0;">${escapeHtml(title)}</h2>
      <div class="view-toggle">${toggleButtons}</div>
    </div>
    ${panels}
  `;
}

function buildChaptersSection(groupId, chapterXml) {
  if (!chapterXml) {
    return '<h2>Chapters</h2><p class="item-sub">No chapters stored.</p>';
  }
  const chapters = parseChapterAtoms(chapterXml);
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

  return buildToggleSection(
    groupId,
    "Chapters",
    [
      {
        key: "table",
        label: "Table",
        html: `<table><thead><tr><th>#</th><th>Title</th><th>Start</th><th>End</th></tr></thead><tbody>${tableRows}</tbody></table>`,
      },
      { key: "file", label: "File", html: `<pre>${escapeHtml(chapterXml)}</pre>` },
    ],
    "table"
  );
}

function buildTrackSection(groupId, tracksJson) {
  if (!tracksJson) {
    return '<h2 style="margin-top:1rem;">Track Metadata</h2><p class="item-sub">No track metadata stored.</p>';
  }
  const parsedTracks = parseMediaInfoTracks(tracksJson);
  let tableRows;
  if (parsedTracks.status === "invalid") {
    tableRows = `<tr><td colspan="7" class="item-sub">Could not parse the stored track metadata as JSON.</td></tr>`;
  } else if (parsedTracks.status === "legacy") {
    tableRows = `<tr><td colspan="7" class="item-sub">This episode's track metadata was saved in an older format. Back it up again to see it here.</td></tr>`;
  } else if (parsedTracks.tracks.length === 0) {
    tableRows = `<tr><td colspan="7" class="item-sub">No video/audio/subtitle tracks found in the stored report.</td></tr>`;
  } else {
    tableRows = parsedTracks.tracks
      .map(
        (t) => `<tr>
              <td>${escapeHtml(String(t.id))}</td>
              <td>${escapeHtml(t.type)}</td>
              <td>${escapeHtml(t.language)}</td>
              <td>${escapeHtml(t.name)}</td>
              <td>${t.default ? "yes" : "no"}</td>
              <td>${t.forced ? "yes" : "no"}</td>
              <td>${escapeHtml(t.format)}</td>
            </tr>`
      )
      .join("");
  }

  return buildToggleSection(
    groupId,
    "Track Metadata",
    [
      {
        key: "table",
        label: "Table",
        html: `<table><thead><tr><th>ID</th><th>Type</th><th>Lang</th><th>Name</th><th>Default</th><th>Forced</th><th>Format</th></tr></thead><tbody>${tableRows}</tbody></table>`,
      },
      { key: "file", label: "File", html: `<pre>${escapeHtml(prettyPrintJson(tracksJson))}</pre>` },
    ],
    "table"
  );
}

function wireToggleGroups(root) {
  root.querySelectorAll("[data-toggle-group]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.dataset.toggleGroup;
      const key = btn.dataset.toggleKey;
      root.querySelectorAll(`[data-toggle-group="${group}"]`).forEach((b) => b.classList.toggle("active", b === btn));
      root.querySelectorAll(`[data-panel-group="${group}"]`).forEach((el) => {
        el.style.display = el.dataset.panelKey === key ? "" : "none";
      });
    });
  });
}

function resetDetailPanel() {
  state.detailKind = null;
  state.detailId = null;
  document.getElementById("detail-panel").innerHTML =
    '<p class="placeholder-text">Select a show or episode to see its details.</p>';
}

async function renderShowOverview(showId) {
  const panel = document.getElementById("detail-panel");
  const show = state.shows.find((s) => s.id === showId);
  if (!show) {
    resetDetailPanel();
    return;
  }
  state.detailKind = "show";
  state.detailId = showId;

  panel.innerHTML = `
    <div class="detail-panel-header">
      <div class="detail-title-wrap">
        <img class="poster-thumb poster-thumb-lg" data-poster-src="/api/shows/${show.id}/poster" alt="">
        <div>
          <h2>${escapeHtml(show.name)}${show.missing ? ' <span class="badge-missing">MISSING</span>' : ""}</h2>
          <p class="item-sub">${escapeHtml(show.path)}</p>
          <p class="item-sub">${show.episode_count} episode(s) • ${show.scanned_count} scanned • ${show.backed_up_count} backed up • ${show.cleaned_count} cleaned</p>
        </div>
      </div>
      <button data-action="close-detail" aria-label="Close" title="Close">×</button>
    </div>
    <div class="item-actions" style="margin: 0.75rem 0;">
      <button data-action="scan-show">${show.scanned_count > 0 ? "Rescan Show" : "Scan Show"}</button>
      <button class="primary" data-action="backup-show">${show.backed_up_count > 0 ? "Re-backup Show" : "Backup Show"}</button>
      <div class="menu-wrap">
        <button type="button" class="menu-toggle" data-action="toggle-menu" aria-label="Actions" title="Actions">☰</button>
        <div class="menu-dropdown" hidden>
          <button data-action="restore-show">Restore Show</button>
          <button data-action="clean-show">${show.cleaned_count > 0 ? "Re-clean Show" : "Clean Show"}</button>
          <button data-action="dryrun-show">Dry Run Show</button>
          <button class="danger" data-action="clear-show">Clear Show</button>
        </div>
      </div>
    </div>
    <p class="item-sub">Expand this show on the left to browse its seasons and episodes.</p>
  `;

  wirePosterImages(panel);
  wireMenus(panel);
  panel.querySelector('[data-action="close-detail"]').addEventListener("click", () => {
    document.querySelectorAll(".show-item.selected").forEach((row) => row.classList.remove("selected"));
    resetDetailPanel();
  });
  panel.querySelector('[data-action="scan-show"]').addEventListener("click", () => {
    if (
      show.scanned_count > 0 &&
      !confirm(`Rescan all episodes in "${show.name}"? This re-extracts chapters and mediainfo from the files on disk, overwriting the current scan data. Backed-up data is not affected.`)
    ) {
      return;
    }
    startJob(`/api/shows/${showId}/scan`, () => afterShowMutation(showId));
  });
  panel.querySelector('[data-action="backup-show"]').addEventListener("click", () => {
    if (
      show.backed_up_count > 0 &&
      !confirm(`Re-backup all episodes in "${show.name}"? This overwrites the existing backup with the current scan data.`)
    ) {
      return;
    }
    startJob(`/api/shows/${showId}/backup`, () => afterShowMutation(showId));
  });
  panel.querySelector('[data-action="restore-show"]').addEventListener("click", () => {
    if (!confirm(`Restore chapters for all episodes in "${show.name}"? This overwrites the MKV files on disk with the stored chapters.`)) {
      return;
    }
    startJob(`/api/shows/${showId}/restore`, () => selectLibrary(state.selectedLibraryId));
  });
  panel.querySelector('[data-action="clean-show"]').addEventListener("click", () => {
    if (
      show.cleaned_count > 0 &&
      !confirm(`Re-clean up all episodes in "${show.name}"? This rewrites track languages/names and container metadata in every MKV file in this show.`)
    ) {
      return;
    }
    startJob(`/api/cleanup/shows/${showId}/clean`, () => afterShowMutation(showId));
  });
  panel.querySelector('[data-action="dryrun-show"]').addEventListener("click", () => {
    startJob(withDryRun(`/api/cleanup/shows/${showId}/clean`), (job) => openDryRunResults(job));
  });
  panel.querySelector('[data-action="clear-show"]').addEventListener("click", async () => {
    if (!confirm(`Clear all backup data for "${show.name}"? This cannot be undone. Files on disk and the scan database are never touched.`)) {
      return;
    }
    try {
      await api(`/api/shows/${showId}`, { method: "DELETE" });
      toast(`Cleared backup for "${show.name}"`, "ok");
      await afterShowMutation(showId);
    } catch (err) {
      toast(`Failed to clear: ${err.message}`, "error");
    }
  });
}

async function renderEpisodeDetail(episodeId) {
  const panel = document.getElementById("detail-panel");
  panel.innerHTML = '<p class="placeholder-text">Loading episode...</p>';
  let ep;
  try {
    ep = await api(`/api/episodes/${episodeId}`);
  } catch (err) {
    toast(`Failed to load episode: ${err.message}`, "error");
    resetDetailPanel();
    return;
  }
  state.detailKind = "episode";
  state.detailId = episodeId;

  const scanPanelHtml = `
    <div class="detail-columns">
      <div>${buildChaptersSection("scan-chapters", ep.chapters)}</div>
      <div>${buildTrackSection("scan-tracks", ep.track_metadata)}</div>
    </div>
  `;
  const backupPanelHtml = ep.has_backup
    ? `
      <div class="detail-columns">
        <div>${buildChaptersSection("backup-chapters", ep.backup_chapters)}</div>
        <div>${buildTrackSection("backup-tracks", ep.backup_track_metadata)}</div>
      </div>
    `
    : '<p class="item-sub">No backup stored yet. Back up this episode to see it here.</p>';

  const hasScan = Boolean(ep.last_scanned_at);
  panel.innerHTML = `
    <div class="detail-panel-header">
      <h2 style="word-break:break-all;">${escapeHtml(ep.filename)}${ep.missing ? ' <span class="badge-missing">MISSING</span>' : ""}</h2>
      <button data-action="close-detail" aria-label="Close" title="Close">×</button>
    </div>
    <p class="item-sub">${escapeHtml(ep.path)}</p>
    <p class="item-sub">Last scanned: ${escapeHtml(formatTimestamp(ep.last_scanned_at))} • Backed up: ${escapeHtml(formatTimestamp(ep.backed_up_at))} • Cleaned up: ${escapeHtml(formatTimestamp(ep.cleaned_at))}</p>
    ${ep.cleanup_ok === false ? `<p class="item-sub" style="color:var(--danger);">Cleanup failed: ${escapeHtml(ep.cleanup_error || "unknown error")}</p>` : ""}
    <div class="item-actions" style="margin: 0.5rem 0 1rem 0;">
      <button data-action="scan-episode">${hasScan ? "Rescan Episode" : "Scan Episode"}</button>
      <button class="primary" data-action="backup-episode" ${hasScan ? "" : "disabled title=\"Scan this episode first\""}>${ep.has_backup ? "Re-backup Episode" : "Backup Episode"}</button>
      <button data-action="restore-episode">Restore Episode</button>
      <button data-action="clean-episode">${ep.has_cleanup ? "Re-clean Episode" : "Clean Episode"}</button>
      <button data-action="dryrun-episode">Dry Run Episode</button>
      <button class="danger" data-action="clear-episode-backup">Clear Episode Backup</button>
    </div>
    <nav class="tabs" style="padding:0; margin-bottom:0.75rem;">
      <button type="button" class="tab-btn active" data-toggle-group="episode-view" data-toggle-key="scan">Scanned Data</button>
      <button type="button" class="tab-btn" data-toggle-group="episode-view" data-toggle-key="backup">Backup Data</button>
    </nav>
    <div data-panel-group="episode-view" data-panel-key="scan">${scanPanelHtml}</div>
    <div data-panel-group="episode-view" data-panel-key="backup" style="display:none;">${backupPanelHtml}</div>
  `;

  wireToggleGroups(panel);
  panel.querySelector('[data-action="close-detail"]').addEventListener("click", () => {
    document.querySelectorAll(".episode-row.selected").forEach((row) => row.classList.remove("selected"));
    resetDetailPanel();
  });
  panel.querySelector('[data-action="scan-episode"]').addEventListener("click", () => {
    if (hasScan && !confirm(`Rescan "${ep.filename}"? This re-extracts chapters and mediainfo from the file on disk, overwriting the current scan data. Backed-up data is not affected.`)) {
      return;
    }
    startJob(`/api/episodes/${episodeId}/scan`, () => {
      renderEpisodeDetail(episodeId);
      if (state.expandedShowIds.has(ep.show_id)) toggleShowEpisodes(ep.show_id, true);
    });
  });
  panel.querySelector('[data-action="backup-episode"]').addEventListener("click", () => {
    if (ep.has_backup && !confirm(`Re-backup "${ep.filename}"? This overwrites the existing backup with the current scan data.`)) {
      return;
    }
    startJob(`/api/episodes/${episodeId}/backup`, () => {
      renderEpisodeDetail(episodeId);
      if (state.expandedShowIds.has(ep.show_id)) toggleShowEpisodes(ep.show_id, true);
    });
  });
  panel.querySelector('[data-action="restore-episode"]').addEventListener("click", () => {
    if (!confirm(`Restore chapters for "${ep.filename}"? This overwrites the file on disk with the stored chapters.`)) {
      return;
    }
    startJob(`/api/episodes/${episodeId}/restore`);
  });
  panel.querySelector('[data-action="clean-episode"]').addEventListener("click", () => {
    if (
      ep.has_cleanup &&
      !confirm(`Re-clean up "${ep.filename}"? This rewrites track languages/names and container metadata in the file on disk.`)
    ) {
      return;
    }
    startJob(`/api/cleanup/episodes/${episodeId}/clean`, () => {
      renderEpisodeDetail(episodeId);
      if (state.expandedShowIds.has(ep.show_id)) toggleShowEpisodes(ep.show_id, true);
    });
  });
  panel.querySelector('[data-action="dryrun-episode"]').addEventListener("click", () => {
    startJob(withDryRun(`/api/cleanup/episodes/${episodeId}/clean`), (job) => openDryRunResults(job));
  });
  panel.querySelector('[data-action="clear-episode-backup"]').addEventListener("click", async () => {
    if (!confirm(`Clear the backup for "${ep.filename}"? This cannot be undone. Files on disk and the scan database are never touched.`)) {
      return;
    }
    try {
      await api(`/api/episodes/${episodeId}`, { method: "DELETE" });
      toast(`Cleared backup for "${ep.filename}"`, "ok");
      await selectLibrary(state.selectedLibraryId);
      renderEpisodeDetail(episodeId);
      if (state.expandedShowIds.has(ep.show_id)) toggleShowEpisodes(ep.show_id, true);
    } catch (err) {
      toast(`Failed to clear: ${err.message}`, "error");
    }
  });
}

function closeModal() {
  document.getElementById("modal-root").innerHTML = "";
}

function openDryRunResults(job) {
  const modalRoot = document.getElementById("modal-root");
  const scopeLabel = job.label.replace(/^Dry run: /, "");

  const episodesHtml = job.results
    .map((r) => {
      if (!r.ok) {
        return `
          <div class="dryrun-episode">
            <h3>${escapeHtml(r.filename)}</h3>
            <p class="item-sub" style="color:var(--danger);">Error: ${escapeHtml(r.error || "unknown error")}</p>
          </div>`;
      }
      const rows = (r.summary || [])
        .map((line) => {
          const idx = line.indexOf(" -> ");
          const field = idx === -1 ? line : line.slice(0, idx);
          const value = idx === -1 ? "" : line.slice(idx + 4);
          return `<tr><td>${escapeHtml(field)}</td><td>${escapeHtml(value) || "<em>cleared</em>"}</td></tr>`;
        })
        .join("");
      const warningsHtml = (r.warnings || []).length
        ? `<p class="item-sub" style="color:var(--danger);">${r.warnings.map(escapeHtml).join("<br>")}</p>`
        : "";
      return `
        <div class="dryrun-episode">
          <h3>${escapeHtml(r.filename)}</h3>
          <table><thead><tr><th>Field</th><th>Would become</th></tr></thead><tbody>${rows}</tbody></table>
          ${warningsHtml}
        </div>`;
    })
    .join("");

  const jobErrorHtml =
    job.status === "error" ? `<p class="item-sub" style="color:var(--danger);">Job failed: ${escapeHtml(job.error)}</p>` : "";

  modalRoot.innerHTML = `
    <div class="modal-backdrop" id="modal-backdrop">
      <div class="modal">
        <div class="modal-header">
          <h3>Dry run: ${escapeHtml(scopeLabel)}</h3>
          <button data-action="close-modal">Close</button>
        </div>
        <p class="item-sub">No files were modified. This previews what "Clean" would change.</p>
        ${jobErrorHtml}
        ${episodesHtml || '<p class="item-sub">No episodes to preview.</p>'}
      </div>
    </div>
  `;

  document.getElementById("modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "modal-backdrop") closeModal();
  });
  modalRoot.querySelector('[data-action="close-modal"]').addEventListener("click", closeModal);
}

function initTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  const viewIds = { library: "view-library", settings: "view-settings" };
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.toggle("active", b === btn));
      for (const [tab, id] of Object.entries(viewIds)) {
        document.getElementById(id).style.display = btn.dataset.tab === tab ? "" : "none";
      }
    });
  });
}

function initSettingsTab() {
  const refreshAfterGlobalOp = () => {
    // loadLibraries() re-selects the current library itself once the fresh
    // library list comes back, so nothing further is needed here.
    loadLibraries();
  };

  document.getElementById("settings-scan-all").addEventListener("click", () => {
    if (!confirm("Scan every library now? This may take a while depending on how large your libraries are.")) {
      return;
    }
    startJob("/api/scan/all", refreshAfterGlobalOp);
  });
  document.getElementById("settings-backup-all").addEventListener("click", () => {
    if (!confirm("Backup every library now? This may take a while depending on how large your libraries are.")) {
      return;
    }
    startJob("/api/backup/all", refreshAfterGlobalOp);
  });
  document.getElementById("settings-restore-all").addEventListener("click", () => {
    if (
      !confirm(
        "Restore chapters for every episode in every library? This overwrites MKV files on disk with the stored chapters and may take a while."
      )
    ) {
      return;
    }
    startJob("/api/restore/all", refreshAfterGlobalOp);
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
    } catch (err) {
      toast(`Failed to clear database: ${err.message}`, "error");
    }
  });

  document.getElementById("add-codec-mapping").addEventListener("click", async () => {
    const keyInput = document.getElementById("new-codec-key");
    const nameInput = document.getElementById("new-codec-name");
    const codec_key = keyInput.value.trim();
    const display_name = nameInput.value.trim();
    if (!codec_key || !display_name) {
      toast("Both a codec identifier and a display name are required", "error");
      return;
    }
    try {
      await api("/api/cleanup/settings/codecs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codec_key, display_name }),
      });
      keyInput.value = "";
      nameInput.value = "";
      toast("Codec mapping added", "ok");
      loadCodecMappings();
    } catch (err) {
      toast(`Failed to add codec mapping: ${err.message}`, "error");
    }
  });

  document.getElementById("save-subtitle-settings").addEventListener("click", async () => {
    const forced_suffix = document.getElementById("forced-suffix-input").value.trim();
    const commentary_suffix = document.getElementById("commentary-suffix-input").value.trim();
    if (!forced_suffix || !commentary_suffix) {
      toast("Both suffixes are required", "error");
      return;
    }
    try {
      await api("/api/cleanup/settings/subtitles", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ forced_suffix, commentary_suffix }),
      });
      toast("Subtitle naming settings saved", "ok");
    } catch (err) {
      toast(`Failed to save: ${err.message}`, "error");
    }
  });

  loadCodecMappings();
  loadSubtitleSettings();
  loadCleanupSteps();
}

const CLEANUP_STEPS = [
  { key: "set_title", label: "Set container title to the filename" },
  { key: "clear_date", label: "Clear the container's date tag" },
  { key: "clear_writing_app", label: "Clear the writing-application tag" },
  { key: "clear_muxing_app", label: "Clear the muxing-application tag" },
  { key: "clear_encoder_tags", label: "Clear encoder library tags (Encoded_Library / Name / Version / Settings)" },
  { key: "force_first_track_japanese", label: "Force the first track to Japanese and clear its name" },
  { key: "set_video_default", label: "Set the default flag on video tracks" },
  { key: "rename_audio_tracks", label: "Rename audio tracks (language, codec, channels)" },
  { key: "rename_subtitle_tracks", label: "Rename subtitle tracks (language, Forced/Commentary suffix)" },
];

async function loadCleanupSteps() {
  const container = document.getElementById("cleanup-steps-list");
  container.textContent = "Loading...";
  try {
    const steps = await api("/api/cleanup/settings/steps");
    renderCleanupSteps(steps);
  } catch (err) {
    container.innerHTML = `<p class="item-sub">Failed to load: ${escapeHtml(err.message)}</p>`;
  }
}

function renderCleanupSteps(steps) {
  const container = document.getElementById("cleanup-steps-list");
  container.innerHTML = CLEANUP_STEPS.map(
    (step) => `
      <label class="cleanup-step-toggle">
        <input type="checkbox" data-step="${step.key}" ${steps[step.key] ? "checked" : ""}>
        ${escapeHtml(step.label)}
      </label>`
  ).join("");

  container.querySelectorAll("[data-step]").forEach((checkbox) =>
    checkbox.addEventListener("change", async () => {
      const key = checkbox.dataset.step;
      try {
        await api("/api/cleanup/settings/steps", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [key]: checkbox.checked }),
        });
      } catch (err) {
        checkbox.checked = !checkbox.checked;
        toast(`Failed to save: ${err.message}`, "error");
      }
    })
  );
}

async function loadCodecMappings() {
  const container = document.getElementById("codec-mapping-table");
  container.textContent = "Loading...";
  try {
    const rows = await api("/api/cleanup/settings/codecs");
    renderCodecMappings(rows);
  } catch (err) {
    container.innerHTML = `<p class="item-sub">Failed to load: ${escapeHtml(err.message)}</p>`;
  }
}

function renderCodecMappings(rows) {
  const container = document.getElementById("codec-mapping-table");
  const body = rows
    .map(
      (row) => `
      <tr data-codec-id="${row.id}">
        <td>${
          row.is_builtin
            ? escapeHtml(row.codec_key)
            : `<input type="text" value="${escapeHtml(row.codec_key)}" data-field="codec_key">`
        }</td>
        <td><input type="text" value="${escapeHtml(row.display_name)}" data-field="display_name"></td>
        <td class="item-actions">
          <button type="button" data-action="save-codec">Save</button>
          ${row.is_builtin ? "" : '<button type="button" class="danger" data-action="delete-codec">Delete</button>'}
        </td>
      </tr>`
    )
    .join("");
  container.innerHTML = `
    <table>
      <thead><tr><th>Codec</th><th>Display Name</th><th></th></tr></thead>
      <tbody>${body || '<tr><td colspan="3" class="item-sub">No codec mappings yet.</td></tr>'}</tbody>
    </table>
  `;

  container.querySelectorAll('[data-action="save-codec"]').forEach((btn) =>
    btn.addEventListener("click", async () => {
      const tr = btn.closest("tr");
      const id = tr.dataset.codecId;
      const displayInput = tr.querySelector('[data-field="display_name"]');
      const keyInput = tr.querySelector('[data-field="codec_key"]');
      const payload = { display_name: displayInput.value.trim() };
      if (keyInput) payload.codec_key = keyInput.value.trim();
      try {
        await api(`/api/cleanup/settings/codecs/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        toast("Saved", "ok");
        loadCodecMappings();
      } catch (err) {
        toast(`Failed to save: ${err.message}`, "error");
      }
    })
  );
  container.querySelectorAll('[data-action="delete-codec"]').forEach((btn) =>
    btn.addEventListener("click", async () => {
      const tr = btn.closest("tr");
      const id = tr.dataset.codecId;
      const codecLabel = tr.children[0].textContent.trim();
      if (!confirm(`Delete the codec mapping for "${codecLabel}"?`)) return;
      try {
        await api(`/api/cleanup/settings/codecs/${id}`, { method: "DELETE" });
        toast("Codec mapping deleted", "ok");
        loadCodecMappings();
      } catch (err) {
        toast(`Failed to delete: ${err.message}`, "error");
      }
    })
  );
}

async function loadSubtitleSettings() {
  try {
    const settings = await api("/api/cleanup/settings/subtitles");
    document.getElementById("forced-suffix-input").value = settings.forced_suffix;
    document.getElementById("commentary-suffix-input").value = settings.commentary_suffix;
  } catch (err) {
    toast(`Failed to load subtitle naming settings: ${err.message}`, "error");
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function initLibrarySelect() {
  document.getElementById("library-select").addEventListener("change", (e) => {
    const id = Number(e.target.value);
    if (id) selectLibrary(id);
  });
}

initTheme();
initTabs();
initLibrarySelect();
initSettingsTab();
loadLibraries();
resumeActiveJobs();

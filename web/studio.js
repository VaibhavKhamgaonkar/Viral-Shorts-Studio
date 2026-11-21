"use strict";

const byId = (id) => document.getElementById(id);
const route = location.pathname === "/settings" ? "settings" : location.pathname === "/library" ? "library" : "studio";
const destinations = ["youtube", "instagram", "facebook"];
const settingsForm = byId("settings-form");
const createForm = byId("create-form");
const dialog = byId("detail-dialog");
let sourceType = "url";
let savedUpload;
let pollId;
let noticeTimer;

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS(svg.namespaceURI, "use");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
}

function action(label, handler, iconName, style = "button-outline") {
    const button = element("button", "button " + style, label);
    button.type = "button";
    if (iconName) button.prepend(icon(iconName));
    button.addEventListener("click", handler);
    return button;
}

function link(label, href, iconName) {
    const anchor = element("a", "button button-outline", label);
    anchor.href = href;
    if (iconName) anchor.prepend(icon(iconName));
    return anchor;
}

async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = Array.isArray(body.detail) ? body.detail.map((item) => item.msg).join(" ") : body.detail;
        throw new Error(detail || "Request failed (" + response.status + ").");
    }
    return response.json();
}

function jsonRequest(method, data) {
    return {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)};
}

function fileRequest(file) {
    const body = new FormData();
    body.set("file", file);
    return {method: "POST", body};
}

function message(id, text = "", success = false) {
    const node = byId(id);
    node.textContent = text;
    node.hidden = !text;
    node.classList.toggle("success", success);
}

function toast(text) {
    message("toast", text);
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => message("toast"), 4300);
}

function showDialog(title, items) {
    byId("dialog-title").textContent = title;
    byId("dialog-content").replaceChildren(...items);
    if (!dialog.open) dialog.showModal();
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        toast("Copied to clipboard.");
    } catch {
        toast("Clipboard unavailable. Select the text and copy it.");
    }
}

function askToDelete(title, details, submit) {
    const perform = action("Delete", async () => {
        perform.disabled = true;
        try {
            await submit();
            dialog.close();
        } catch (error) {
            toast(error.message);
        } finally {
            perform.disabled = false;
        }
    }, null, "button-danger");
    const controls = element("div", "dialog-actions");
    controls.append(action("Cancel", () => dialog.close()), perform);
    showDialog(title, [element("p", null, details), controls]);
}

async function loadAI() {
    try {
        const ai = await api("/api/ai");
        const states = {ready: "Local AI ready", missing_model: "Model missing", offline: "Ollama offline"};
        byId("ai-status").textContent = states[ai.status];
        byId("ai-model").textContent = ai.model;
        byId("ai-dot").classList.toggle("ready", ai.status === "ready");
        document.querySelector(".engine").title = states[ai.status] + " · " + ai.model;
    } catch {
        byId("ai-status").textContent = "AI status unavailable";
    }
}

async function loadStorage() {
    try {
        const storage = await api("/api/storage");
        byId("cache-label").textContent = storage.total_mb ? "Cache · " + storage.total_mb + " MB" : "Download cache";
        return storage;
    } catch {
        byId("cache-label").textContent = "Download cache";
        return null;
    }
}

async function openCache() {
    const storage = await loadStorage();
    const content = element("p", null, storage
        ? storage.count + " files · " + storage.total_mb + " MB of downloaded source media and captions."
        : "Download cache usage is unavailable.");
    const controls = element("div", "dialog-actions");
    controls.append(action("Open folder", async () => {
        try { await api("/api/open_downloads_folder"); } catch (error) { toast(error.message); }
    }, "folder"));
    if (storage?.count) {
        controls.append(action("Clear cache", () => askToDelete("Clear download cache?",
            "Downloaded media and captions will be removed. Exported clips stay in your library.",
            async () => {
                const result = await api("/api/clear_downloads", {method: "POST"});
                toast(result.count + " cache files removed.");
                await loadStorage();
            })));
    }
    showDialog("Download cache", [content, controls]);
}

function setSource(type) {
    sourceType = type;
    const fromUrl = type === "url";
    byId("source-url").setAttribute("aria-pressed", fromUrl);
    byId("source-upload").setAttribute("aria-pressed", !fromUrl);
    byId("url-panel").hidden = !fromUrl;
    byId("upload-panel").hidden = fromUrl;
    byId("youtube-url").required = fromUrl;
    byId("video-file").required = !fromUrl;
    message("form-message");
}

function showRangeOptions() {
    const exact = byId("selection").value === "range";
    byId("range-options").hidden = !exact;
    byId("length-options").hidden = exact;
    byId("clip-start").required = exact;
    byId("clip-end").required = exact;
    byId("clip-count-field").hidden = exact;
    byId("job-count").disabled = exact;
}

function setNavCollapsed(collapsed) {
    document.documentElement.dataset.navCollapsed = String(collapsed);
    const toggle = byId("nav-toggle");
    const label = collapsed ? "Expand navigation" : "Collapse navigation";
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
}

function parseTime(value) {
    const raw = value.trim();
    const parts = raw.split(":");
    if (!/^\d+(?::[0-5]?\d){0,2}(?:\.\d+)?$/.test(raw)
        || (parts.length === 3 && Number(parts[1]) >= 60)) {
        throw new Error("Enter timestamps as seconds or hh:mm:ss.");
    }
    return parts.reduce((total, part) => total * 60 + Number(part), 0);
}

function formatTime(value) {
    const seconds = Math.max(0, Math.floor(value));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds / 60) % 60;
    const remainder = String(seconds % 60).padStart(2, "0");
    return hours ? hours + ":" + String(minutes).padStart(2, "0") + ":" + remainder : minutes + ":" + remainder;
}

function showProgress(stage, detail, status) {
    byId("job-progress").hidden = false;
    if (byId("job-stage").textContent !== stage) byId("job-stage").textContent = stage;
    if (byId("job-detail").textContent !== detail) byId("job-detail").textContent = detail;
    byId("job-indicator").className = "job-indicator" + (status ? " " + status : "");
}

async function startJob(event) {
    event.preventDefault();
    byId("generate").disabled = true;
    message("form-message");
    const request = {
        source_type: sourceType,
        selection: byId("selection").value,
        clip_length: byId("clip-length").value,
        mode: byId("layout").value,
        auto_delete_source: byId("auto-delete").checked,
        mirror: byId("mirror").checked,
        color_grade: byId("color-grade").checked,
        output_resolution: Number(byId("resolution").value),
        playback_speed: Number(byId("playback-speed").value),
        captions: byId("job-captions").checked,
        opening_hook: byId("job-opening-hook").checked,
        audio_mode: byId("job-audio").value || null,
    };
    try {
        if (request.selection === "range") {
            request.start_time = parseTime(byId("clip-start").value);
            request.end_time = parseTime(byId("clip-end").value);
            if (request.end_time - request.start_time < 1 || request.end_time - request.start_time > 600) {
                throw new Error("Choose a time range between 1 second and 10 minutes.");
            }
        } else {
            request.clip_count = byId("job-count").valueAsNumber;
        }
        if (sourceType === "upload") {
            const file = byId("video-file").files[0];
            if (!file) throw new Error("Choose a video to upload.");
            if (savedUpload?.file !== file) {
                showProgress("Uploading video", "Copying " + file.name + " into the local workspace.");
                const upload = await api("/api/upload_video", fileRequest(file));
                savedUpload = {file, path: upload.file_path};
            }
            request.file_path = savedUpload.path;
        } else {
            request.url = byId("youtube-url").value.trim();
        }
        const job = await api("/api/generate", jsonRequest("POST", request));
        sessionStorage.setItem("studio-job", job.job_id);
        if (request.auto_delete_source) savedUpload = null;
        pollJob(job.job_id);
    } catch (error) {
        if (byId("job-stage").textContent === "Uploading video") byId("job-progress").hidden = true;
        message("form-message", error.message);
        byId("generate").disabled = false;
    }
}

async function pollJob(id) {
    clearTimeout(pollId);
    byId("generate").disabled = true;
    try {
        const job = await api("/api/job/" + encodeURIComponent(id));
        const active = job.status === "queued" || job.status === "running";
        showProgress(job.stage, job.detail, active ? "" : job.status === "completed" ? "done" : "failed");
        if (active) {
            pollId = setTimeout(() => pollJob(id), 1400);
            return;
        }
        await Promise.all([loadTasks(), loadStorage()]);
    } catch (error) {
        showProgress("Job status unavailable", error.message, "failed");
    }
    sessionStorage.removeItem("studio-job");
    byId("generate").disabled = false;
}

function addCopySection(container, title, content) {
    if (!content) return;
    const section = element("section", "dialog-section");
    section.append(element("h3", null, title), element("p", null, content),
        action("Copy", () => copyText(content), "copy"));
    container.push(section);
}

function openPostCopy(clip) {
    const contents = [];
    const sourceLabels = {ai: "Drafted by the local model", transcript: "Drafted from the transcript", source: "Based on source details"};
    if (sourceLabels[clip.copy_source]) contents.push(element("p", "section-copy", sourceLabels[clip.copy_source]));
    addCopySection(contents, "Title", clip.title);
    for (const [index, variant] of (clip.title_variants || []).entries()) addCopySection(contents, "Alternative title " + (index + 1), variant);
    addCopySection(contents, "Opening text", clip.hook);
    addCopySection(contents, "Description", clip.description);
    addCopySection(contents, "Pinned comment", clip.pinned_comment);
    const tags = (clip.tags || []).map((tag) => "#" + tag.replace(/^#+/, "").replace(/\s+/g, "")).join(" ");
    addCopySection(contents, "Hashtags", tags);
    addCopySection(contents, "Music brief · use to source or generate a track", clip.music_brief);
    addCopySection(contents, "Suggested library track", clip.music_track);
    if (clip.applied_music) contents.push(element("p", "section-copy", "Audio replacement used: " + clip.applied_music));
    if (clip.selection === "ai" && clip.reason) contents.push(element("p", "section-copy", "Why this highlight: " + clip.reason));
    showDialog("Posting copy", contents);
}

function clipCard(clip, folder) {
    const uri = "/clips/" + encodeURIComponent(folder) + "/" + encodeURIComponent(clip.filename);
    const card = element("article", "clip");
    const video = element("video");
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.src = uri;
    video.setAttribute("aria-label", clip.title);
    const body = element("div", "clip-body");
    body.append(element("strong", "clip-title", clip.title));
    if (Number.isFinite(clip.start_time) && Number.isFinite(clip.end_time)) {
        const labels = {ai: "AI highlight", timeline: "Timeline pick", manual: "Exact range"};
        const range = formatTime(clip.start_time) + " – " + formatTime(clip.end_time);
        body.append(element("p", "clip-time", range + (labels[clip.selection] ? " · " + labels[clip.selection] : "")));
    }
    const actions = element("div", "clip-actions");
    const download = link("MP4", uri, "download");
    download.download = clip.filename;
    actions.append(action("Posting copy", () => openPostCopy(clip), "copy"), download);
    body.append(actions);
    card.append(video, body);
    return card;
}

function batchCard(batch) {
    const card = element("article", "batch");
    const heading = element("div", "batch-heading");
    heading.append(element("h3", null, batch.source_title));
    if (/^https?:\/\/(?:[^/]+\.)?(?:youtube\.com|youtu\.be)\//i.test(batch.youtube_url || "")) {
        const source = link("Source", batch.youtube_url, "link");
        source.target = "_blank";
        source.rel = "noopener noreferrer";
        heading.append(source);
    }
    const grid = element("div", "batch-grid");
    grid.append(...batch.clips.map((clip) => clipCard(clip, batch.folder_name)));
    const buttons = element("div", "batch-toolbar");
    const folder = encodeURIComponent(batch.folder_name);
    buttons.append(
        action("Open folder", async () => {
            try { await api("/api/open_folder/" + folder); } catch (error) { toast(error.message); }
        }, "folder"),
        action("Clear source copy", () => askToDelete("Clear this source copy?",
            "This removes the source media saved locally for this batch.",
            async () => {
                const result = await api("/api/delete_source/" + folder, {method: "POST"});
                toast(result.deleted_count ? "Local source copy cleared." : "No local source copy was found.");
                await loadStorage();
            })),
        action("Delete batch", () => askToDelete("Delete this clip batch?",
            "The exported clips, captions, and posting copy for this batch will be removed.",
            async () => {
                await api("/api/delete_task/" + folder, {method: "POST"});
                toast("Batch deleted.");
                await loadTasks();
            }))
    );
    card.append(heading, grid, buttons);
    return card;
}

async function loadTasks() {
    byId("refresh").disabled = true;
    try {
        const tasks = await api("/api/tasks");
        const count = tasks.reduce((total, batch) => total + batch.clips.length, 0);
        byId("clip-count").textContent = count + (count === 1 ? " exported clip" : " exported clips");
        byId("library-empty").hidden = count !== 0;
        byId("clip-batches").replaceChildren(...tasks.map(batchCard));
        message("library-message");
    } catch (error) {
        byId("clip-count").textContent = "Library unavailable";
        message("library-message", error.message);
    } finally {
        byId("refresh").disabled = false;
    }
}

async function loadTracks(selected) {
    const tracks = await api("/api/music");
    const input = byId("music-track");
    input.replaceChildren(new Option(tracks.length ? "Choose a track" : "No tracks added", ""));
    for (const track of tracks) input.add(new Option(track, track));
    input.value = tracks.includes(selected) ? selected : "";
    byId("music-library-count").textContent = tracks.length + (tracks.length === 1 ? " track" : " tracks") + " in your local library";
}

function showTrackField() {
    const enabled = byId("music-mode").value === "replace";
    byId("music-track-field").hidden = !enabled;
    byId("music-track").required = enabled;
}

async function loadPreferences() {
    const preferences = await api("/api/settings");
    if (route === "studio") {
        byId("clip-length").value = preferences.clip_length;
        byId("layout").value = preferences.default_mode;
        byId("auto-delete").checked = preferences.auto_delete_source;
        byId("job-count").value = preferences.max_clips_to_generate;
        byId("job-captions").checked = preferences.captions;
        byId("job-opening-hook").checked = preferences.opening_hook;
    }
    if (route !== "settings") return;
    for (const field of settingsForm.elements) {
        if (!field.name || field.name === "music_track") continue;
        if (field.type === "checkbox") field.checked = preferences[field.name];
        else field.value = preferences[field.name];
    }
    for (const platform of destinations) {
        const entry = preferences.destinations[platform];
        byId(platform + "-account").value = entry?.account || "";
        byId(platform + "-enabled").checked = Boolean(entry?.enabled);
    }
    showTrackField();
    await loadTracks(preferences.music_track);
}

async function savePreferences(event) {
    event.preventDefault();
    const button = byId("save-settings");
    button.disabled = true;
    const data = {destinations: {}};
    for (const field of settingsForm.elements) {
        if (!field.name) continue;
        data[field.name] = field.type === "checkbox" ? field.checked
            : field.type === "number" ? Number(field.value) : field.value;
    }
    for (const platform of destinations) {
        data.destinations[platform] = {
            account: byId(platform + "-account").value,
            enabled: byId(platform + "-enabled").checked,
        };
    }
    try {
        await api("/api/settings", jsonRequest("PUT", data));
        message("settings-message", "Saved. These defaults apply to new jobs.", true);
        await loadAI();
    } catch (error) {
        message("settings-message", error.message);
    } finally {
        button.disabled = false;
    }
}

async function addMusic() {
    const input = byId("music-file");
    const file = input.files[0];
    if (!file) {
        toast("Choose an audio file first.");
        return;
    }
    const button = byId("add-music");
    button.disabled = true;
    try {
        const result = await api("/api/music", fileRequest(file));
        await loadTracks(result.filename);
        input.value = "";
        toast("Track added to your library.");
    } catch (error) {
        toast(error.message);
    } finally {
        button.disabled = false;
    }
}

const pages = {
    studio: ["Create clips", "Find the best moments in a long video and export them as vertical clips."],
    library: ["Clip library", "Review exports, download MP4s, and copy text for your posts."],
    settings: ["Studio settings", "Customize new exports and configure your publishing destinations."],
};
const [title, subtitle] = pages[route];
document.querySelector('[data-nav="' + route + '"]').setAttribute("aria-current", "page");
byId("page-title").textContent = title;
byId("breadcrumb").textContent = title;
byId("page-description").textContent = subtitle;
document.title = title + " | Viral Shorts Studio";
byId("clip-workspace").hidden = route === "settings";
settingsForm.hidden = route !== "settings";
byId("create-panel").hidden = route !== "studio";
byId("clip-workspace").classList.toggle("library-only", route === "library");
if (route === "library") byId("library-title").textContent = "Exports";

setNavCollapsed(localStorage.getItem("studio-nav-collapsed") === "true");
byId("nav-toggle").addEventListener("click", () => {
    const collapsed = byId("nav-toggle").getAttribute("aria-expanded") === "true";
    setNavCollapsed(collapsed);
    localStorage.setItem("studio-nav-collapsed", String(collapsed));
});
byId("cache-button").addEventListener("click", openCache);
byId("source-url").addEventListener("click", () => setSource("url"));
byId("source-upload").addEventListener("click", () => setSource("upload"));
byId("selection").addEventListener("change", showRangeOptions);
byId("refresh").addEventListener("click", loadTasks);
byId("start-link").addEventListener("click", (event) => {
    if (route !== "studio") return;
    event.preventDefault();
    byId(sourceType === "url" ? "youtube-url" : "video-file").focus();
});
createForm.addEventListener("submit", startJob);
settingsForm.addEventListener("submit", savePreferences);
byId("music-mode").addEventListener("change", showTrackField);
byId("add-music").addEventListener("click", addMusic);

loadPreferences().catch((error) => message(route === "settings" ? "settings-message" : "form-message", error.message));
loadAI();
loadStorage();
if (route !== "settings") {
    loadTasks();
    const activeJob = sessionStorage.getItem("studio-job");
    if (activeJob) pollJob(activeJob);
}

"use strict";

const comparisonVideo = byId("editor-original-video");
const timelineTracks = byId("editor-timeline-tracks");
const comparisonStatus = byId("editor-comparison-status");
let selectedPictureRange;
let observedPictureRange;
let renderedComparison;
let renderedFrame;
let activeMarkers = [];

function openEditorComparison(uri, session) {
    observedPictureRange = undefined;
    comparisonVideo.src = uri;
    api("/api/edit/" + session.path + "/media").then(data => {
        if (editSession === session) session.fps = data.fps;
    }).catch(() => {});
}

function closeEditorComparison() {
    comparisonVideo.pause();
    comparisonVideo.removeAttribute("src");
    comparisonVideo.load();
    activeMarkers = [];
    renderedComparison = undefined;
    cancelAnimationFrame(renderedFrame);
}

function syncOriginal(time, playing) {
    if (!comparisonVideo.readyState || !Number.isFinite(time)) return;
    if (Math.abs(comparisonVideo.currentTime - time) > (playing ? .10 : .005)) {
        comparisonVideo.currentTime = Math.min(comparisonVideo.duration, Math.max(0, time));
    }
    if (playing && comparisonVideo.paused) comparisonVideo.play().catch(() => {});
    if (!playing && !comparisonVideo.paused) comparisonVideo.pause();
}

function activeEditorPicture() {
    return pictureRangeAtPlayhead() || {
        look: byId("editor-look").value, frame: byId("editor-frame-choice").value,
        zoom: Number(byId("editor-zoom").value), mirror: byId("editor-mirror").checked,
    };
}

function pictureRangeAtPlayhead() {
    if (!editSession) return undefined;
    const elapsed = editorVideo.currentTime - editorRange().start;
    return editSession.ranges.find(range => elapsed >= range.at - .0005 && elapsed < range.end - .0005);
}

function updateEditorComparison(start, elapsed, valid) {
    if (!editSession || !editorDialog.open) return;
    byId("editor-current-frame").textContent = valid
        ? "Frame " + (Math.floor((elapsed + .0005) / editorFrameStep()) + 1) + " · " + elapsed.toFixed(3) + " s"
        : "";
    for (const marker of activeMarkers) {
        marker.button.setAttribute("aria-current", String(elapsed >= marker.at && elapsed < marker.end));
    }
    if (!valid || renderedComparison) return;
    const range = pictureRangeAtPlayhead();
    if (range !== observedPictureRange) {
        observedPictureRange = range;
        updateEditorPicture();
    }
    syncOriginal(start + elapsed, editorIsPlaying());
}

function setRenderedComparison(edit) {
    cancelAnimationFrame(renderedFrame);
    renderedComparison = edit || undefined;
    if (!edit) return;
    timelineTracks.hidden = true;
    comparisonStatus.hidden = false;
    comparisonStatus.textContent = "During an insert, Original pauses at the cut while Edited plays the cutaway.";
}

function renderedSourcePosition(time, edit, assets) {
    let offset = 0;
    for (const cut of [...edit.inserts].sort((left, right) => left.at - right.at)) {
        const start = cut.at + offset;
        const duration = assets.get(cut.asset).duration;
        if (time < start) break;
        if (time < start + duration) return {time: edit.start + cut.at, insideInsert: true};
        offset += duration;
    }
    return {time: edit.start + time - offset, insideInsert: false};
}

function tickRenderedComparison() {
    cancelAnimationFrame(renderedFrame);
    if (!renderedComparison || !editSession || !editorDialog.open) return;
    const position = renderedSourcePosition(renderedVideo.currentTime, renderedComparison, editSession.insertAssets);
    const playing = !renderedVideo.paused && !renderedVideo.seeking && renderedVideo.readyState >= 3;
    syncOriginal(position.time, playing && !position.insideInsert);
    if (playing) renderedFrame = requestAnimationFrame(tickRenderedComparison);
}
for (const event of ["playing", "pause", "seeking", "seeked", "waiting", "ended"]) {
    renderedVideo.addEventListener(event, tickRenderedComparison);
}
comparisonVideo.addEventListener("loadedmetadata", () => {
    if (renderedComparison) tickRenderedComparison();
    else if (editorDialog.open) updateEditorTransport();
});

function jumpToEditorCue(at, end = at) {
    const {start, end: trimEnd, valid} = editorRange();
    if (!valid || !Number.isFinite(at)) return;
    clearRenderedPreview();
    editorVideo.pause();
    const offset = end > at ? Math.min(.25, (end - at) / 2) : 0;
    editorVideo.currentTime = Math.min(trimEnd, Math.max(start, start + at + offset));
    updateEditorTransport();
    if (matchMedia("(max-width: 850px)").matches) byId("editor-preview-title").scrollIntoView({block: "start"});
}

function visibleEditorCaptions() {
    if (!editSession) return [];
    const {start, end, valid} = editorRange();
    if (!valid) return [];
    return (editSession.captionLines || []).filter(line => line.end > start && line.start < end).map(line => ({
        at: Math.max(0, line.start - start), end: Math.min(end, line.end) - start, text: line.text,
    }));
}

function showCaptionIndicator(active) {
    if (renderedComparison) return;
    timelineTracks.hidden = timelineTracks.childElementCount === 0;
    comparisonStatus.hidden = !editSession.captions.length;
    if (!comparisonStatus.hidden) {
        const status = active ? "Caption visible in the Edited pane." : "No caption at this time. Select a caption marker to jump to speech.";
        if (comparisonStatus.textContent !== status) comparisonStatus.textContent = status;
    }
}

function renderEditorTimeline() {
    if (!editSession) return;
    const {start, end, valid} = editorRange();
    const tracks = [
        ["captions", "Captions", visibleEditorCaptions()],
        ["titles", "Titles", editSession.titles],
        ["picture", "Picture", editSession.ranges.map(range => ({...range, text: "Picture effect"}))],
        ["inserts", "Inserts", editSession.inserts.map(cut => ({at: cut.at, end: cut.at, text: editSession.insertAssets.get(cut.asset).label}))],
    ];
    const previous = byId("editor-caption-select").value;
    byId("editor-caption-select").replaceChildren(...tracks[0][2].map((line, index) =>
        new Option(editorClock(line.at) + " · " + line.text, String(index))));
    if (byId("editor-caption-select").options[Number(previous)]) byId("editor-caption-select").value = previous || "0";
    byId("editor-caption-list").hidden = tracks[0][2].length === 0;
    activeMarkers = [];
    timelineTracks.replaceChildren();
    timelineTracks.hidden = !valid || Boolean(renderedComparison);
    if (!valid) return;
    const duration = end - start;
    for (const [type, label, cues] of tracks) {
        if (!cues.length) continue;
        const track = element("div", "editor-timeline-track");
        track.dataset.track = type;
        const bar = element("div", "editor-timeline-bar");
        track.append(element("span", null, label), bar);
        for (const cue of cues) {
            if (!Number.isFinite(cue.at) || cue.at < 0 || cue.at >= duration) continue;
            const marker = element("button", "editor-timeline-cue");
            marker.type = "button";
            marker.title = editorClock(cue.at) + " · " + cue.text;
            marker.setAttribute("aria-label", "Jump to " + marker.title);
            marker.style.left = Math.min(98, cue.at / duration * 100) + "%";
            marker.style.width = Math.max(0, (Math.min(duration, cue.end) - cue.at) / duration * 100) + "%";
            marker.addEventListener("click", () => jumpToEditorCue(cue.at, cue.end));
            bar.append(marker);
            activeMarkers.push({button: marker, at: cue.at, end: cue.end});
        }
        timelineTracks.append(track);
    }
    timelineTracks.hidden = !timelineTracks.childElementCount || Boolean(renderedComparison);
}

byId("editor-jump-caption").addEventListener("click", () => {
    const line = visibleEditorCaptions()[Number(byId("editor-caption-select").value)];
    if (line) jumpToEditorCue(line.at, line.end);
});

function editorFrameStep() {
    return 1 / (editSession?.fps || 30);
}

for (const [id, direction] of [["editor-prev-frame", -1], ["editor-next-frame", 1]]) {
    byId(id).addEventListener("click", () => {
        const {start, end, valid} = editorRange();
        if (!valid) return;
        const step = editorFrameStep();
        const frame = Math.round((editorVideo.currentTime - start) / step);
        jumpToEditorCue(Math.max(0, Math.min(end - start, (frame + direction) * step)));
    });
}

function loadRangeOptions(assets) {
    byId("editor-range-look").replaceChildren(...assets.looks.map(look => new Option(look.label, look.id)));
    byId("editor-range-frame").replaceChildren(new Option("None", ""), ...assets.frames.map(frame => new Option(frame.label, frame.id)));
}

function resetEditorRange() {
    selectedPictureRange = undefined;
    byId("editor-add-range").textContent = "Apply range effect";
    byId("editor-reset-range").hidden = true;
}

function renderPictureRanges() {
    byId("editor-picture-ranges").replaceChildren(...editSession.ranges.map(range => {
        const row = element("div", "editor-visual-cue");
        const look = [...byId("editor-range-look").options].find(option => option.value === range.look)?.text || range.look;
        const frame = [...byId("editor-range-frame").options].find(option => option.value === range.frame)?.text;
        const name = action(frame && range.frame ? frame : look, () => {
            selectedPictureRange = range;
            byId("editor-range-start").value = range.at.toFixed(3);
            byId("editor-range-end").value = range.end.toFixed(3);
            byId("editor-range-look").value = range.look;
            byId("editor-range-frame").value = range.frame;
            byId("editor-range-zoom").value = range.zoom;
            byId("editor-range-zoom-value").textContent = range.zoom + "%";
            byId("editor-range-mirror").checked = range.mirror;
            byId("editor-add-range").textContent = "Update range effect";
            byId("editor-reset-range").hidden = false;
            jumpToEditorCue(range.at, range.end);
        }, null, "button-plain");
        name.classList.add("editor-visual-name");
        row.append(name, element("span", "editor-hint", range.at.toFixed(3)), element("span", "editor-hint", range.end.toFixed(3)),
            removeVisualCue("picture range", () => {
                editSession.ranges.splice(editSession.ranges.indexOf(range), 1);
                if (selectedPictureRange === range) resetEditorRange();
                renderPictureRanges();
            }));
        return row;
    }));
    updateEditorPicture();
    renderEditorTimeline();
}

function sourceEditorOffset() {
    const {start, end, valid} = editorRange();
    return valid ? Math.max(0, Math.min(end - start, editorVideo.currentTime - start)) : 0;
}
byId("editor-mark-range-start").addEventListener("click", () => { byId("editor-range-start").value = sourceEditorOffset().toFixed(3); });
byId("editor-mark-range-end").addEventListener("click", () => { byId("editor-range-end").value = sourceEditorOffset().toFixed(3); });
byId("editor-one-frame").addEventListener("click", () => {
    const {start, end, valid} = editorRange();
    if (!valid) return;
    const step = editorFrameStep();
    const at = Math.max(0, Math.min(end - start - step, Math.round(sourceEditorOffset() / step) * step));
    byId("editor-range-start").value = at.toFixed(3);
    byId("editor-range-end").value = (at + step).toFixed(3);
});
byId("editor-range-zoom").addEventListener("input", ({target}) => { byId("editor-range-zoom-value").textContent = target.value + "%"; });
byId("editor-reset-range").addEventListener("click", resetEditorRange);
byId("editor-add-range").addEventListener("click", () => {
    if (!editSession || !editorRange().valid) return;
    const at = byId("editor-range-start").valueAsNumber;
    const end = byId("editor-range-end").valueAsNumber;
    const length = editorRange().end - editorRange().start;
    if (!Number.isFinite(at) || !Number.isFinite(end) || at < 0 || end <= at || end > length + .002) {
        message("editor-status", "Choose a range inside the trimmed video.");
        return;
    }
    if (editSession.ranges.some(range => range !== selectedPictureRange && at < range.end && end > range.at)) {
        message("editor-status", "This range overlaps another effect. Change the start or end.");
        return;
    }
    if (!selectedPictureRange && editSession.ranges.length >= 8) {
        message("editor-status", "An edit can include up to eight picture ranges.");
        return;
    }
    const picture = {
        at, end, look: byId("editor-range-look").value, frame: byId("editor-range-frame").value,
        zoom: Number(byId("editor-range-zoom").value), mirror: byId("editor-range-mirror").checked,
    };
    if (selectedPictureRange) Object.assign(selectedPictureRange, picture);
    else editSession.ranges.push(picture);
    resetEditorRange();
    jumpToEditorCue(at, end);
    renderPictureRanges();
    message("editor-status");
});

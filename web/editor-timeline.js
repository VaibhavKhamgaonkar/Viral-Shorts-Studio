"use strict";

const comparisonVideo = byId("editor-original-video");
const timelineTracks = byId("editor-timeline-tracks");
const comparisonStatus = byId("editor-comparison-status");
const focusPicker = byId("editor-focus-picker");
const focusToggle = byId("editor-focus-toggle");
const focusStatus = byId("editor-focus-status");
const focusGuide = byId("editor-focus-guide");
let selectedPictureRange;
let observedPictureRange;
let focusDraft = false;
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
    if (focusDraft) return rangePicture();
    return pictureRangeAtPlayhead() || {
        look: byId("editor-look").value, frame: byId("editor-frame-choice").value,
        zoom: Number(byId("editor-zoom").value), mirror: byId("editor-mirror").checked, focus_x: 50, focus_y: 50,
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
    timelineTracks.style.setProperty("--playhead", (valid ? 100 * elapsed / (editorRange().end - start) : 0) + "%");
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
    endFocusDraft();
    timelineTracks.hidden = true;
    comparisonStatus.hidden = false;
    comparisonStatus.textContent = edit.inserts.length
        ? "Original pauses during cutaways."
        : edit.cuts.length ? "Edited plays only the sections you kept." : "";
    comparisonStatus.hidden = !comparisonStatus.textContent;
}

function renderedSourcePosition(time, edit, assets) {
    let offset = 0;
    const events = [...edit.cuts, ...edit.inserts].sort((left, right) => left.at - right.at);
    for (const event of events) {
        const start = event.at + offset;
        if (time < start) break;
        if (!event.asset) {
            offset -= event.end - event.at;
            continue;
        }
        const duration = assets.get(event.asset).duration;
        if (time < start + duration) return {time: edit.start + event.at, insideInsert: true};
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
        at: Math.max(0, line.start - start), end: Math.min(end, line.end) - start, text: line.text, sourceIndex: line.sourceIndex,
    }));
}

function showCaptionIndicator(active) {
    if (renderedComparison) return;
    timelineTracks.hidden = timelineTracks.childElementCount === 0;
    const removed = cutAtSourceTime();
    comparisonStatus.hidden = !removed && !editSession.captions.length;
    if (!comparisonStatus.hidden) {
        const status = removed ? "This section is marked for removal."
            : active ? "Caption visible in the Edited pane." : "No caption at this time. Select a caption marker to jump to speech.";
        if (comparisonStatus.textContent !== status) comparisonStatus.textContent = status;
    }
}

function renderEditorTimeline() {
    if (!editSession) return;
    const {start, end, valid} = editorRange();
    const tracks = [
        ["source", "Source", valid ? editorSourceSections(end - start) : []],
        ["cuts", "Cuts", editSession.cuts.map(cut => ({...cut, text: "Removed section"}))],
        ["captions", "Captions", visibleEditorCaptions()],
        ["titles", "Titles", editSession.titles],
        ["picture", "Picture", editSession.ranges.map(range => ({...range, text: hasOffCenterFocus(range) ? "Focus crop" : "Picture effect"}))],
        ["inserts", "Inserts", editSession.inserts.map(cut => ({at: cut.at, end: cut.at, text: editSession.insertAssets.get(cut.asset).label}))],
        ["sounds", "Sounds", editSession.cues.map(cue => ({at: cue.at, end: cue.at, text: cue.label}))],
    ];
    const captionSelect = byId("editor-caption-select");
    const previous = captionSelect.value;
    captionSelect.replaceChildren(...editSession.captions.map((line, index) =>
        new Option(editorClock(line.start) + " · " + line.text.slice(0, 100), String(index))));
    if (captionSelect.options[Number(previous)]) captionSelect.value = previous || "0";
    byId("editor-caption-list").hidden = !editSession.captions.length;
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
            marker.style.left = "min(" + (cue.at / duration * 100) + "%, calc(100% - 8px))";
            marker.style.width = Math.max(0, (Math.min(duration, cue.end) - cue.at) / duration * 100) + "%";
            marker.addEventListener("click", () => {
                const tool = {source: "cut", cuts: "cut", captions: "text", titles: "text",
                    picture: "picture", inserts: "inserts", sounds: "audio"}[type];
                showEditorTool(tool);
                if (type === "captions") chooseCaption(cue.sourceIndex);
                if (type === "source") selectSourceSection(cue.at, cue.end);
                if (type === "picture") byId("editor-range-effects").open = true;
                if (type === "titles") byId("editor-animated-titles").open = true;
                jumpToEditorCue(cue.at, cue.end);
            });
            bar.append(marker);
            activeMarkers.push({button: marker, at: cue.at, end: cue.end});
        }
        timelineTracks.append(track);
    }
    timelineTracks.hidden = !timelineTracks.childElementCount || Boolean(renderedComparison);
}

byId("editor-jump-caption").addEventListener("click", () => {
    const index = Number(byId("editor-caption-select").value);
    const line = visibleEditorCaptions().find(line => line.sourceIndex === index);
    chooseCaption(index);
    if (line) jumpToEditorCue(line.at, line.end);
    else byId("editor-caption-status").textContent = "This caption is outside the current trim.";
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

function rangePicture() {
    return {
        look: byId("editor-range-look").value, frame: byId("editor-range-frame").value,
        zoom: byId("editor-range-zoom").valueAsNumber, mirror: byId("editor-range-mirror").checked,
        focus_x: byId("editor-focus-x").valueAsNumber, focus_y: byId("editor-focus-y").valueAsNumber,
    };
}

function hasOffCenterFocus(picture) {
    return picture.zoom > 100 && (picture.focus_x !== 50 || picture.focus_y !== 50);
}

function focusCrop(picture) {
    const size = 10000 / picture.zoom;
    const half = size / 2;
    const center = coordinate => Math.min(100 - half, Math.max(half, coordinate));
    return {size, x: center(picture.focus_x), y: center(picture.focus_y)};
}

function updateFocusGuide({size, x, y}) {
    focusGuide.hidden = !focusDraft;
    if (!focusDraft) return;
    focusGuide.style.width = focusGuide.style.height = size + "%";
    focusGuide.style.left = (x - size / 2) + "%";
    focusGuide.style.top = (y - size / 2) + "%";
}

function updateRangeZoom() {
    byId("editor-range-zoom-value").textContent = byId("editor-range-zoom").value + "%";
}

function setFocusPoint(x, y) {
    for (const [axis, value] of [["x", x], ["y", y]]) {
        byId("editor-focus-" + axis).value = Math.round(value);
        byId("editor-focus-" + axis + "-value").textContent = Math.round(value) + "%";
    }
}

function setFocusPicker(open) {
    focusPicker.hidden = !open;
    focusToggle.setAttribute("aria-pressed", String(open));
    focusStatus.hidden = !focusDraft;
    focusStatus.textContent = open ? "Select a subject in the Original preview."
        : focusDraft ? "Focus preview. Apply the range effect to use this crop in your edit." : "";
}

function endFocusDraft() {
    focusDraft = false;
    focusGuide.hidden = true;
    setFocusPicker(false);
    byId("editor-reset-range").hidden = !selectedPictureRange;
}

function previewRangeFocus() {
    if (!editorRange().valid) return;
    clearRenderedPreview();
    editorVideo.pause();
    if (rangePicture().zoom === 100) byId("editor-range-zoom").value = 160;
    focusDraft = true;
    const cancel = byId("editor-reset-range");
    cancel.hidden = false;
    cancel.textContent = selectedPictureRange ? "Cancel range edit" : "Cancel focus preview";
    setFocusPicker(!focusPicker.hidden);
    updateRangeZoom();
    updateEditorPicture();
}

focusToggle.addEventListener("click", () => {
    if (!focusPicker.hidden) {
        setFocusPicker(false);
        return;
    }
    previewRangeFocus();
    if (!focusDraft) return;
    setFocusPicker(true);
    focusPicker.focus();
});
focusPicker.addEventListener("click", event => {
    const bounds = focusPicker.getBoundingClientRect();
    const toPercent = (point, origin, size) => Math.min(100, Math.max(0, (point - origin) / size * 100));
    setFocusPoint(event.detail ? toPercent(event.clientX, bounds.left, bounds.width) : 50,
        event.detail ? toPercent(event.clientY, bounds.top, bounds.height) : 50);
    setFocusPicker(false);
    focusToggle.focus({preventScroll: true});
    updateEditorPicture();
});
focusPicker.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    setFocusPicker(false);
    focusToggle.focus();
});
for (const axis of ["x", "y"]) {
    byId("editor-focus-" + axis).addEventListener("input", () => {
        const {focus_x, focus_y} = rangePicture();
        setFocusPoint(focus_x, focus_y);
        previewRangeFocus();
    });
}
byId("editor-focus-center").addEventListener("click", () => {
    setFocusPoint(50, 50);
    previewRangeFocus();
});
byId("editor-range-effects").addEventListener("toggle", ({target}) => {
    if (target.open || !focusDraft) return;
    endFocusDraft();
    updateEditorPicture();
});
editorVideo.addEventListener("playing", () => {
    if (!focusDraft) return;
    endFocusDraft();
    updateEditorPicture();
});

function resetEditorRange() {
    selectedPictureRange = undefined;
    endFocusDraft();
    setFocusPoint(50, 50);
    byId("editor-add-range").textContent = "Apply range effect";
    byId("editor-reset-range").hidden = true;
}

function renderPictureRanges() {
    byId("editor-picture-ranges").replaceChildren(...editSession.ranges.map(range => {
        const row = element("div", "editor-visual-cue");
        const look = [...byId("editor-range-look").options].find(option => option.value === range.look)?.text || range.look;
        const frame = [...byId("editor-range-frame").options].find(option => option.value === range.frame)?.text;
        const appearance = frame && range.frame ? frame : look;
        const name = action(hasOffCenterFocus(range) ? "Focus crop · " + appearance : appearance, () => {
            endFocusDraft();
            selectedPictureRange = range;
            byId("editor-range-start").value = range.at.toFixed(3);
            byId("editor-range-end").value = range.end.toFixed(3);
            byId("editor-range-look").value = range.look;
            byId("editor-range-frame").value = range.frame;
            byId("editor-range-zoom").value = range.zoom;
            updateRangeZoom();
            setFocusPoint(range.focus_x, range.focus_y);
            byId("editor-range-mirror").checked = range.mirror;
            byId("editor-add-range").textContent = "Update range effect";
            const cancel = byId("editor-reset-range");
            cancel.hidden = false;
            cancel.textContent = "Cancel range edit";
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
for (const id of ["editor-range-zoom", "editor-range-look", "editor-range-frame", "editor-range-mirror"]) {
    byId(id).addEventListener("input", () => {
        updateRangeZoom();
        if (focusDraft) updateEditorPicture();
    });
}
byId("editor-reset-range").addEventListener("click", () => {
    resetEditorRange();
    updateEditorPicture();
});
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
    const picture = {at, end, ...rangePicture()};
    if (selectedPictureRange) Object.assign(selectedPictureRange, picture);
    else editSession.ranges.push(picture);
    resetEditorRange();
    jumpToEditorCue(at, end);
    renderPictureRanges();
    message("editor-status");
});

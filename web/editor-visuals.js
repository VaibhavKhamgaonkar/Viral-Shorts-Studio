"use strict";

const editorFrame = byId("editor-frame-overlay");
const previewFrameContainer = editorFrame.parentElement;
const titleOverlay = byId("editor-text-overlay");
const captionOverlay = byId("editor-caption-preview");
const renderedVideo = byId("editor-full-preview");
const renderPreviewButton = byId("editor-render-preview");
const reactionMenu = byId("editor-reaction");
const reactionVideo = byId("editor-reaction-video");
const reactionImage = byId("editor-reaction-image");
let renderedPreviewRequest;
let renderedPreviewUrl;
let selectedTitle;

function clearRenderedPreview() {
    renderedPreviewRequest?.abort();
    renderedPreviewRequest = undefined;
    if (renderedPreviewUrl) {
        renderedVideo.pause();
        renderedVideo.removeAttribute("src");
        renderedVideo.load();
        URL.revokeObjectURL(renderedPreviewUrl);
        renderedPreviewUrl = undefined;
    }
    renderedVideo.hidden = true;
    editorVideo.hidden = false;
    byId("editor-live-transport").hidden = false;
    byId("editor-preview-title").textContent = "Live preview";
    renderPreviewButton.textContent = "Preview full edit";
    renderPreviewButton.disabled = !editorRange().valid;
    if (editSession) {
        showEditorFrame();
        updateEditorTransport();
    }
}

function releaseReactionPreview() {
    reactionImage.removeAttribute("src");
    reactionVideo.pause();
    reactionVideo.removeAttribute("src");
    reactionVideo.load();
}

function resetEditorVisuals() {
    selectedTitle = undefined;
    clearRenderedPreview();
    releaseReactionPreview();
    reactionImage.hidden = reactionVideo.hidden = true;
    byId("editor-caption-status").textContent = "";
    byId("editor-clear-captions").hidden = true;
    byId("editor-generate-captions").disabled = false;
    byId("editor-reaction-hint").textContent = "";
    byId("editor-add-title").textContent = "Add title at playhead";
    reactionMenu.replaceChildren(new Option("Loading reactions…", ""));
    byId("editor-frame-choice").replaceChildren(new Option("None", ""));
    byId("editor-look").value = "original";
    renderVisualCues();
}

function loadEditorVisualAssets(assets) {
    for (const look of assets.looks) editSession.looks.set(look.id, look.css);
    byId("editor-look").replaceChildren(...assets.looks.map(look => new Option(look.label, look.id)));
    for (const frame of assets.frames) byId("editor-frame-choice").add(new Option(frame.label, frame.id));
    reactionMenu.replaceChildren();
    for (const insert of assets.inserts) addReactionChoice(insert);
    showReaction();
    updateEditorPicture();
}

function addReactionChoice(asset) {
    editSession.insertAssets.set(asset.id, asset);
    reactionMenu.add(new Option(asset.label, asset.id));
}

function showEditorFrame() {
    const value = byId("editor-frame-choice").value;
    previewFrameContainer.dataset.effect = value;
    editorFrame.hidden = !value || !renderedVideo.hidden;
    const src = value ? "/api/editor-frames/" + encodeURIComponent(value) : "";
    if (src && editorFrame.getAttribute("src") !== src) editorFrame.src = src;
    else if (!src) editorFrame.removeAttribute("src");
}

function showReaction() {
    releaseReactionPreview();
    const asset = editSession.insertAssets.get(reactionMenu.value);
    reactionImage.hidden = !asset?.still;
    reactionVideo.hidden = !asset || asset.still;
    if (!asset) return;
    const media = asset.still ? reactionImage : reactionVideo;
    media.src = "/api/editor-inserts/" + encodeURIComponent(asset.id);
    if (asset.still) reactionImage.alt = asset.label + " animation";
    byId("editor-reaction-hint").textContent = asset.hint + " · " + asset.duration.toFixed(2) + " sec";
}

function setVisualTime(input, seconds) {
    input.type = "number";
    input.step = ".01";
    input.min = "0";
    input.value = seconds.toFixed(2);
}

function renderVisualCues() {
    titleOverlay.replaceChildren(...editSession.titles.map(title => {
        const label = element("div", "editor-title-overlay", title.text);
        label.dataset.position = title.position;
        label.dataset.color = title.color;
        label.hidden = true;
        return label;
    }));
    byId("editor-title-cues").replaceChildren(...editSession.titles.map((title, index) => {
        const row = element("div", "editor-visual-cue");
        const name = action(title.text, () => {
            clearRenderedPreview();
            selectedTitle = title;
            byId("editor-title-text").value = title.text;
            byId("editor-title-motion").value = title.animation;
            byId("editor-title-position").value = title.position;
            byId("editor-title-color").value = title.color;
            byId("editor-add-title").textContent = "Update title";
            if (editorRange().valid) editorVideo.currentTime = editorRange().start + title.at;
            updateEditorTransport();
            byId("editor-title-text").focus();
        }, null, "button-plain");
        name.classList.add("editor-visual-name");
        name.title = "Edit title";
        const timeInputs = ["at", "end"].map(field => {
            const input = element("input");
            setVisualTime(input, title[field]);
            input.setAttribute("aria-label", "Title " + (index + 1) + (field === "at" ? " start" : " end") + " in seconds");
            input.addEventListener("input", () => { title[field] = input.valueAsNumber; updateEditorTransport(); });
            return input;
        });
        row.append(name, ...timeInputs, removeVisualCue("title", () => {
            editSession.titles.splice(index, 1);
            if (selectedTitle === title) resetTitleDraft();
        }));
        return row;
    }));
    byId("editor-insert-cues").replaceChildren(...editSession.inserts.map((cut, index) => {
        const row = element("div", "editor-visual-cue editor-insert-cue");
        const asset = editSession.insertAssets.get(cut.asset);
        const input = element("input");
        setVisualTime(input, cut.at);
        input.setAttribute("aria-label", asset.label + " insert time in seconds");
        input.addEventListener("input", () => { cut.at = input.valueAsNumber; updateEditorTransport(); });
        const name = element("strong", "editor-visual-name", asset.label);
        row.append(name, input, element("span", "editor-hint", "+" + asset.duration.toFixed(2) + "s"),
            removeVisualCue("insert", () => editSession.inserts.splice(index, 1)));
        return row;
    }));
    updateEditorTransport();
}

function removeVisualCue(label, remove) {
    const button = action("", () => {
        clearRenderedPreview();
        remove();
        renderVisualCues();
    }, "close");
    button.setAttribute("aria-label", "Remove " + label);
    return button;
}

function resetTitleDraft() {
    selectedTitle = undefined;
    byId("editor-title-text").value = "";
    byId("editor-add-title").textContent = "Add title at playhead";
}

function updateEditorVisuals(start, length, valid) {
    if (!editSession) return;
    const relative = editorVideo.currentTime - start;
    renderPreviewButton.disabled = !valid || Boolean(renderedPreviewRequest);
    byId("editor-add-title").disabled = !valid || (!selectedTitle && editSession.titles.length >= 8);
    byId("editor-add-reaction").disabled = !valid || !reactionMenu.value || editSession.inserts.length >= 4;
    if (!renderedVideo.hidden) {
        titleOverlay.hidden = captionOverlay.hidden = editorFrame.hidden = true;
        return;
    }
    titleOverlay.hidden = false;
    for (const [index, title] of editSession.titles.entries()) {
        const view = titleOverlay.children[index];
        if (!view) continue;
        view.hidden = !valid || !(relative >= title.at && relative < title.end);
        if (view.hidden) continue;
        const duration = title.end - title.at;
        const intro = Math.min(.24, duration / 2);
        const progress = Math.min(1, (relative - title.at) / intro);
        view.style.opacity = Math.min(1, (relative - title.at) / (title.animation === "fade" ? intro : .06),
            (title.end - relative) / Math.min(.15, duration / 3));
        view.style.setProperty("--title-scale", title.animation === "pop" ? .65 + .35 * progress : 1);
        view.style.setProperty("--title-slide", title.animation === "slide" ? (1 - progress) * 7 + "cqh" : "0px");
    }
    const time = editorVideo.currentTime;
    const caption = valid && editSession.captionLines?.find(line => time >= line.start && time < line.end);
    captionOverlay.hidden = !caption;
    if (caption && captionOverlay.textContent !== caption.text) captionOverlay.textContent = caption.text;
    const total = byId("editor-extended-time");
    total.hidden = !valid || editSession.inserts.length === 0;
    if (!total.hidden) {
        const added = editSession.inserts.reduce((sum, cut) => sum + editSession.insertAssets.get(cut.asset).duration, 0);
        total.textContent = "+" + added.toFixed(2) + " sec inserted · " + (length + added).toFixed(2) + " sec total";
    }
}

function validateEditorVisuals(session, duration) {
    if (session.titles.some(title => !Number.isFinite(title.at) || !Number.isFinite(title.end) ||
            title.at < 0 || title.end > duration + .01 || title.end - title.at < .19)) {
        return "Give each title at least 0.2 seconds inside the trim range.";
    }
    if (session.inserts.some(cut => !Number.isFinite(cut.at) || cut.at < 0 || cut.at >= duration)) {
        return "Put each insert before the end of the trimmed clip.";
    }
    return "";
}

editorForm.addEventListener("input", () => {
    if (renderedPreviewRequest || renderedPreviewUrl) clearRenderedPreview();
    message("editor-status");
});
byId("editor-frame-choice").addEventListener("change", showEditorFrame);
reactionMenu.addEventListener("change", showReaction);

byId("editor-add-title").addEventListener("click", () => {
    const {start, end, valid} = editorRange();
    const text = byId("editor-title-text").value.trim();
    if (!valid || !text) {
        byId("editor-title-text").focus();
        return;
    }
    clearRenderedPreview();
    const length = end - start;
    const at = Math.max(0, Math.min(length - .22, editorVideo.currentTime - start));
    const content = {text, animation: byId("editor-title-motion").value,
        position: byId("editor-title-position").value, color: byId("editor-title-color").value};
    if (selectedTitle) Object.assign(selectedTitle, content);
    else if (editSession.titles.length < 8) editSession.titles.push({
        ...content, at: Number(at.toFixed(2)), end: Number(Math.min(length, at + 2.5).toFixed(2)),
    });
    resetTitleDraft();
    renderVisualCues();
});

byId("editor-add-reaction").addEventListener("click", () => {
    const {start, end, valid} = editorRange();
    if (!valid || !reactionMenu.value || editSession.inserts.length >= 4) return;
    clearRenderedPreview();
    const at = Math.max(0, Math.min(end - start - .05, editorVideo.currentTime - start));
    editSession.inserts.push({asset: reactionMenu.value, at: Number(at.toFixed(2))});
    renderVisualCues();
});

byId("editor-import-reaction").addEventListener("click", () => byId("editor-reaction-file").click());
byId("editor-reaction-file").addEventListener("change", async ({target}) => {
    const file = target.files[0];
    const session = editSession;
    if (!file) return;
    try {
        const asset = await api("/api/editor-inserts", fileRequest(file));
        if (editSession !== session) return;
        addReactionChoice(asset);
        reactionMenu.value = asset.id;
        showReaction();
        updateEditorTransport();
    } catch (error) {
        if (editSession === session) message("editor-status", error.message);
    } finally {
        target.value = "";
    }
});

function setSpeechCaptions(captions) {
    editSession.captions = captions;
    editSession.captionLines = captions.flatMap(line => {
        const groups = line.text.match(/\S+(?:\s+\S+){0,6}/g) || [];
        const length = (line.end - line.start) / groups.length;
        return groups.map((text, index) => ({text, start: line.start + index * length, end: line.start + (index + 1) * length}));
    });
    byId("editor-clear-captions").hidden = captions.length === 0;
    updateEditorTransport();
}

byId("editor-generate-captions").addEventListener("click", async ({currentTarget}) => {
    const session = editSession;
    const status = byId("editor-caption-status");
    currentTarget.disabled = true;
    status.textContent = "Whisper is reading the clip audio…";
    try {
        const query = new URLSearchParams({revision: session.clip.file_revision || ""});
        const result = await api("/api/edit/" + session.path + "/captions?" + query, {method: "POST"});
        if (editSession !== session) return;
        clearRenderedPreview();
        setSpeechCaptions(result.captions);
        status.textContent = result.message || result.captions.length + " speech segments ready for preview and export.";
    } catch (error) {
        if (editSession === session) status.textContent = error.message;
    } finally {
        if (editSession === session) currentTarget.disabled = false;
    }
});
byId("editor-clear-captions").addEventListener("click", () => {
    clearRenderedPreview();
    setSpeechCaptions([]);
    byId("editor-caption-status").textContent = "";
    byId("editor-generate-captions").focus();
});

renderPreviewButton.addEventListener("click", async () => {
    if (renderedPreviewUrl) {
        clearRenderedPreview();
        return;
    }
    const edit = editorPayload();
    if (!edit) return;
    editorVideo.pause();
    const request = renderedPreviewRequest = new AbortController();
    const session = editSession;
    renderPreviewButton.disabled = true;
    renderPreviewButton.textContent = "Rendering preview…";
    message("editor-status");
    try {
        const response = await fetch("/api/edit/" + session.path + "/preview", {
            ...jsonRequest("POST", edit), signal: request.signal,
        });
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            throw new Error(typeof problem.detail === "string" ? problem.detail : "Could not render this preview.");
        }
        const media = await response.blob();
        if (renderedPreviewRequest !== request || editSession !== session) return;
        renderedPreviewRequest = undefined;
        renderedPreviewUrl = URL.createObjectURL(media);
        renderedVideo.src = renderedPreviewUrl;
        renderedVideo.hidden = false;
        editorVideo.hidden = true;
        byId("editor-live-transport").hidden = true;
        byId("editor-preview-title").textContent = "Rendered preview";
        renderPreviewButton.textContent = "Back to live preview";
        updateEditorTransport();
    } catch (error) {
        if (renderedPreviewRequest !== request) return;
        clearRenderedPreview();
        message("editor-status", error.message);
    }
});

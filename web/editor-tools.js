"use strict";

const editorTabs = [...byId("editor-tabs").querySelectorAll('[role="tab"]')];

function showEditorTool(name) {
    for (const tab of editorTabs) {
        const active = tab.id === "editor-tab-" + name;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        byId(tab.getAttribute("aria-controls")).hidden = !active;
    }
    if (name !== "picture" && focusDraft) {
        endFocusDraft();
        updateEditorPicture();
    }
    editorForm.querySelector(".editor-inspector-body").scrollTop = 0;
}

for (const [index, tab] of editorTabs.entries()) {
    tab.addEventListener("click", () => showEditorTool(tab.id.slice("editor-tab-".length)));
    tab.addEventListener("keydown", event => {
        const next = {ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: editorTabs.length - 1}[event.key];
        if (next === undefined) return;
        event.preventDefault();
        const target = editorTabs[(next + editorTabs.length) % editorTabs.length];
        target.click();
        target.focus();
    });
}

function editLength(length, cuts, inserts, assets) {
    const removed = cuts.reduce((total, cut) => total + Math.max(0, Math.min(length, cut.end) - cut.at), 0);
    const added = inserts.reduce((total, insert) => total + assets.get(insert.asset).duration, 0);
    return Math.max(0, length - removed + added);
}

function cutProblem(cuts, length, inserts) {
    let cursor = 0;
    let removed = 0;
    for (const cut of cuts) {
        if (!Number.isFinite(cut.at) || !Number.isFinite(cut.end) || cut.at < cursor ||
            cut.end - cut.at < .099 || cut.end > length + .005) {
            return "Use at least 0.1 seconds inside the trim and avoid overlapping cuts.";
        }
        if ((cut.at > cursor && cut.at - cursor < .049) || (length > cut.end && length - cut.end < .049)) {
            return "Leave 0.05 seconds or more between cuts and trim points.";
        }
        if (inserts.some(insert => cut.at <= insert.at && insert.at < cut.end)) {
            return "Move the reaction insert out of the section you want to remove.";
        }
        cursor = cut.end;
        removed += Math.min(length, cut.end) - cut.at;
    }
    return length - removed < .24 ? "Keep at least 0.24 seconds of video." : "";
}

function cutAtSourceTime() {
    const elapsed = editorVideo.currentTime - editorRange().start;
    return editSession?.cuts.find(cut => elapsed >= cut.at && elapsed < cut.end);
}

function renderEditorCuts() {
    const list = byId("editor-cut-cues");
    list.replaceChildren(...editSession.cuts.map(cut => {
        const row = element("div", "editor-cut-row");
        const jump = action(editorClock(cut.at) + " – " + editorClock(cut.end),
            () => jumpToEditorCue(cut.at, cut.end), null, "button-plain");
        const restore = action("Restore section", () => {
            clearRenderedPreview();
            editSession.cuts = editSession.cuts.filter(item => item !== cut);
            renderEditorCuts();
            byId("editor-add-cut").focus();
        }, null, "button-outline");
        row.append(jump, restore);
        return row;
    }));
    renderEditorTimeline();
    updateEditorTransport();
}

function editorSourceSections(duration) {
    const cuts = editSession.cuts;
    const edges = new Set([0, duration, ...editSession.splits, ...cuts.flatMap(cut => [cut.at, cut.end])]);
    const times = [...edges].filter(time => time >= 0 && time <= duration).sort((a, b) => a - b);
    return times.slice(1).flatMap((end, index) => {
        const at = times[index];
        return cuts.some(cut => at >= cut.at && at < cut.end) ? [] : [{at, end, text: "Source section " + (index + 1)}];
    });
}

function selectSourceSection(at, end) {
    byId("editor-cut-start").value = at.toFixed(3);
    byId("editor-cut-end").value = end.toFixed(3);
    byId("editor-cut-status").textContent = "Section selected on the timeline.";
}

byId("editor-split").addEventListener("click", () => {
    const {start, end, valid} = editorRange();
    if (!valid) return;
    const at = Number(sourceEditorOffset().toFixed(3));
    const section = editorSourceSections(end - start).find(section => at > section.at && at < section.end);
    const status = byId("editor-cut-status");
    if (!section || at - section.at < .1 || section.end - at < .1 || editSession.splits.length >= 24) {
        status.textContent = "Split inside a kept section, at least 0.1 seconds from either edge. Up to 24 split marks are available.";
        return;
    }
    editSession.splits.push(at);
    byId("editor-clear-splits").hidden = false;
    renderEditorTimeline();
    status.textContent = "Split added. Select a section on the Source track.";
});

byId("editor-clear-splits").addEventListener("click", () => {
    editSession.splits = [];
    byId("editor-clear-splits").hidden = true;
    renderEditorTimeline();
    byId("editor-cut-status").textContent = "Split marks cleared.";
});

function resetEditorTools() {
    showEditorTool("cut");
    byId("editor-range-effects").open = false;
    byId("editor-animated-titles").open = false;
    byId("editor-cut-status").textContent = "";
    byId("editor-clear-splits").hidden = true;
    byId("editor-caption-draft").hidden = true;
    renderEditorCuts();
}

for (const edge of ["start", "end"]) {
    byId("editor-trim-" + edge + "-here").addEventListener("click", () => {
        if (!Number.isFinite(editorVideo.duration)) return;
        const field = byId("editor-" + edge);
        field.value = editorVideo.currentTime.toFixed(2);
        field.dispatchEvent(new Event("input", {bubbles: true}));
    });
    byId("editor-mark-cut-" + edge).addEventListener("click", () => {
        if (editorRange().valid) byId("editor-cut-" + edge).value = sourceEditorOffset().toFixed(3);
    });
}

byId("editor-add-cut").addEventListener("click", () => {
    const {start, end, valid} = editorRange();
    if (!valid) return;
    const at = byId("editor-cut-start").valueAsNumber;
    const stop = byId("editor-cut-end").valueAsNumber;
    const cuts = [...editSession.cuts, {at, end: stop}].sort((a, b) => a.at - b.at);
    const problem = cuts.length > 12 ? "An edit can contain up to 12 removed sections." : cutProblem(cuts, end - start, editSession.inserts);
    const status = byId("editor-cut-status");
    status.textContent = problem;
    if (problem) return;
    clearRenderedPreview();
    editSession.cuts = cuts;
    byId("editor-cut-start").value = byId("editor-cut-end").value = "";
    renderEditorCuts();
    jumpToEditorCue(stop);
    status.textContent = "Section removed. Restore it below or review the join in the rendered preview.";
});

function showCaptionDraft(index) {
    const {start, end, valid} = editorRange();
    if (!valid) return;
    const caption = editSession.captions[index];
    const at = Math.min(Math.max(start, end - .21), Math.max(start, editorVideo.currentTime));
    const draft = byId("editor-caption-draft");
    draft.dataset.index = String(index);
    draft.hidden = false;
    byId("editor-caption-text").value = caption?.text || "";
    byId("editor-caption-start").value = (caption?.start ?? at).toFixed(3);
    byId("editor-caption-end").value = (caption?.end ?? Math.min(end, at + 1.5)).toFixed(3);
    byId("editor-remove-caption").hidden = !caption;
    byId("editor-caption-status").textContent = "";
}

function chooseCaption(index) {
    byId("editor-caption-select").value = String(index);
    showCaptionDraft(index);
}

byId("editor-caption-select").addEventListener("change", ({target}) => chooseCaption(Number(target.value)));
byId("editor-new-caption").addEventListener("click", () => {
    showCaptionDraft(-1);
    byId("editor-caption-text").focus();
});
byId("editor-cancel-caption").addEventListener("click", () => {
    byId("editor-caption-draft").hidden = true;
    byId("editor-new-caption").focus();
});

byId("editor-save-caption").addEventListener("click", () => {
    const start = byId("editor-caption-start").valueAsNumber;
    const end = byId("editor-caption-end").valueAsNumber;
    const text = byId("editor-caption-text").value.trim();
    const index = Number(byId("editor-caption-draft").dataset.index);
    const status = byId("editor-caption-status");
    const captions = editSession.captions;
    if (!text || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > editorVideo.duration + .001 || end - start < .199) {
        status.textContent = "Enter text and give the caption at least 0.2 seconds within the original clip.";
        return;
    }
    if (captions.some((caption, number) => number !== index && start < caption.end - .001 && end > caption.start + .001)) {
        status.textContent = "Choose times that do not overlap another caption.";
        return;
    }
    if (index < 0 && captions.length >= 1500) {
        status.textContent = "This edit already contains 1,500 captions.";
        return;
    }
    const saved = {text, start, end};
    const updated = captions.filter((_, number) => number !== index);
    updated.push(saved);
    updated.sort((a, b) => a.start - b.start);
    clearRenderedPreview();
    setSpeechCaptions(updated);
    chooseCaption(updated.indexOf(saved));
    if (end > editorRange().start && start < editorRange().end) {
        jumpToEditorCue(Math.max(0, start - editorRange().start), end - editorRange().start);
    }
    status.textContent = "Caption saved.";
});

byId("editor-remove-caption").addEventListener("click", () => {
    const index = Number(byId("editor-caption-draft").dataset.index);
    clearRenderedPreview();
    setSpeechCaptions(editSession.captions.filter((_, number) => number !== index));
    byId("editor-caption-status").textContent = "Caption deleted.";
    byId("editor-new-caption").focus();
});

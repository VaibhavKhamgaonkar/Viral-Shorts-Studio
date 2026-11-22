"use strict";

const voiceoverMenu = byId("editor-voiceover");
const voiceAudition = byId("editor-voice-audition");
const timedAudioStatus = byId("editor-audio-status");
const sourceTrackStatus = byId("editor-separator-status");
let selectedAudioRegion;

function voiceoverUrl(id) {
    return "/api/editor-voiceovers/" + encodeURIComponent(id);
}

function addVoiceoverAsset(asset) {
    editSession.voiceovers.set(asset.id, asset);
    if (![...voiceoverMenu.options].some(option => option.value === asset.id)) {
        voiceoverMenu.add(new Option(asset.label + " · " + asset.duration.toFixed(1) + " sec", asset.id));
    }
}

function updateVoiceoverChoice() {
    voiceAudition.pause();
    const id = voiceoverMenu.value;
    if (id) voiceAudition.src = voiceoverUrl(id);
    else voiceAudition.removeAttribute("src");
    voiceAudition.load();
    voiceAudition.hidden = !id;
    byId("editor-voice-level-field").hidden = !id;
    byId("editor-audio-fit").hidden = !id;
}

function setAudioRegionDraft(region) {
    selectedAudioRegion = region;
    byId("editor-save-audio").textContent = region ? "Update audio range" : "Apply audio to range";
    byId("editor-cancel-audio").hidden = !region;
    byId("editor-audio-at").value = region?.at ?? Number(sourceEditorOffset().toFixed(3));
    byId("editor-audio-end").value = region?.end ?? "";
    byId("editor-audio-source-level").value = region?.source_level ?? 0;
    byId("editor-voice-level").value = region?.level ?? 100;
    voiceoverMenu.value = region?.asset || "";
    updateRegionLevels();
    updateVoiceoverChoice();
    timedAudioStatus.textContent = "";
}

function resetEditorAudioTools() {
    voiceoverMenu.replaceChildren(new Option("None · adjust original only", ""));
    setAudioRegionDraft();
    byId("editor-voice-synthesis").open = false;
    byId("editor-generate-voice").disabled = false;
    byId("editor-separate-audio").disabled = false;
    byId("editor-voice-help").textContent = "Uses voices installed on this Windows PC.";
    sourceTrackStatus.textContent = "";
    byId("editor-separated-tracks").hidden = true;
    renderAudioRegions();
}

function currentAudioRegion() {
    const at = sourceEditorOffset();
    return editSession?.audioRegions.find(region => at >= region.at && at < region.end);
}

function audioRangeProblem(regions, length) {
    let cursor = 0;
    for (const region of regions) {
        if (!Number.isFinite(region.at) || !Number.isFinite(region.end) || region.at < cursor ||
            region.end - region.at < .049 || region.end > length + .005) {
            return "Use at least 0.05 seconds inside the trim and avoid overlapping audio ranges.";
        }
        cursor = region.end;
    }
    return "";
}

function openAudioRegion(region) {
    showEditorTool("audio");
    setAudioRegionDraft(region);
    jumpToEditorCue(region.at, region.end);
}

function renderAudioRegions() {
    byId("editor-audio-regions").replaceChildren(...editSession.audioRegions.map(region => {
        const row = element("div", "editor-cut-row");
        const asset = editSession.voiceovers.get(region.asset);
        const caption = asset?.label || (region.source_level ? "Lower original" : "Mute original");
        const timing = editorClock(region.at) + "–" + editorClock(region.end);
        const edit = action(caption + " · " + timing, () => openAudioRegion(region), null, "button-plain");
        edit.classList.add("editor-visual-name");
        const remove = removeVisualCue("audio range", () => {
            editSession.audioRegions = editSession.audioRegions.filter(item => item !== region);
            if (selectedAudioRegion === region) setAudioRegionDraft();
            renderAudioRegions();
            updateEditorLevels();
            scheduleEditorSounds();
            byId("editor-save-audio").focus();
        });
        row.append(edit, remove);
        return row;
    }));
    renderEditorTimeline();
}

function updateRegionLevels() {
    byId("editor-audio-source-value").textContent = byId("editor-audio-source-level").value + "%";
    byId("editor-voice-value").textContent = byId("editor-voice-level").value + "%";
}

function fitAudioRange() {
    const {start, end, valid} = editorRange();
    const asset = editSession.voiceovers.get(voiceoverMenu.value);
    if (!asset || !valid) return;
    const at = Math.min(Math.max(0, end - start - .05), Math.max(0, byId("editor-audio-at").valueAsNumber || 0));
    const stop = Math.min(end - start, at + asset.duration);
    byId("editor-audio-at").value = at.toFixed(3);
    byId("editor-audio-end").value = stop.toFixed(3);
    timedAudioStatus.textContent = stop - at + .005 < asset.duration ? "Only the portion that fits this trim will play." : "";
}

function selectNewVoiceover(asset, fit) {
    addVoiceoverAsset(asset);
    voiceoverMenu.value = asset.id;
    updateVoiceoverChoice();
    if (fit) fitAudioRange();
}

voiceoverMenu.addEventListener("change", () => {
    updateVoiceoverChoice();
    if (!Number.isFinite(byId("editor-audio-end").valueAsNumber)) fitAudioRange();
});
byId("editor-audio-fit").addEventListener("click", fitAudioRange);
for (const [edge, id] of [["start", "editor-audio-at"], ["end", "editor-audio-end"]]) {
    byId("editor-audio-mark-" + edge).addEventListener("click", () => {
        byId(id).value = sourceEditorOffset().toFixed(3);
    });
}
for (const id of ["editor-audio-source-level", "editor-voice-level"]) byId(id).addEventListener("input", updateRegionLevels);

byId("editor-import-voice").addEventListener("click", () => byId("editor-voice-file").click());
byId("editor-voice-file").addEventListener("change", async ({target}) => {
    const session = editSession;
    const file = target.files[0];
    if (!file) return;
    target.value = "";
    const button = byId("editor-import-voice");
    button.disabled = true;
    timedAudioStatus.textContent = "Importing audio…";
    try {
        const asset = await api("/api/editor-voiceovers", fileRequest(file));
        if (editSession !== session) return;
        timedAudioStatus.textContent = "";
        selectNewVoiceover(asset, !Number.isFinite(byId("editor-audio-end").valueAsNumber));
    } catch (error) {
        if (editSession === session) timedAudioStatus.textContent = error.message;
    } finally {
        if (editSession === session) button.disabled = false;
    }
});

byId("editor-voice-synthesis").addEventListener("toggle", async ({target}) => {
    const session = editSession;
    if (!target.open || !session || session.voicesLoaded) return;
    const voiceChoices = byId("editor-voice-choice");
    voiceChoices.replaceChildren(new Option("Windows default", ""));
    const help = byId("editor-voice-help");
    try {
        const data = await api("/api/editor-voices");
        if (editSession !== session) return;
        for (const voice of data.voices) voiceChoices.add(new Option(voice.name + " · " + voice.language, voice.name));
        if (!data.voices.length) {
            help.textContent = data.message || "No Windows desktop speech voices were found. Import a recording to continue.";
            byId("editor-generate-voice").disabled = true;
        }
        session.voicesLoaded = true;
    } catch (error) {
        if (editSession === session) help.textContent = error.message;
    }
});

byId("editor-generate-voice").addEventListener("click", async ({currentTarget}) => {
    const session = editSession;
    const text = byId("editor-voice-text").value.trim();
    if (!text) {
        byId("editor-voice-text").focus();
        return;
    }
    const help = byId("editor-voice-help");
    currentTarget.disabled = true;
    help.textContent = "Generating speech locally…";
    try {
        const asset = await api("/api/editor-voices", jsonRequest("POST", {
            text, voice: byId("editor-voice-choice").value, rate: Number(byId("editor-voice-rate").value),
        }));
        if (editSession !== session) return;
        selectNewVoiceover(asset, !Number.isFinite(byId("editor-audio-end").valueAsNumber));
        help.textContent = "Voice-over ready. Listen to it above, then apply it to the range.";
    } catch (error) {
        if (editSession === session) help.textContent = error.message;
    } finally {
        if (editSession === session) currentTarget.disabled = false;
    }
});

byId("editor-separate-audio").addEventListener("click", async ({currentTarget}) => {
    const session = editSession;
    const trim = editorRange();
    const at = byId("editor-audio-at").valueAsNumber;
    const end = byId("editor-audio-end").valueAsNumber;
    const length = end - at;
    if (!trim.valid || !Number.isFinite(length) || at < 0 || end > trim.end - trim.start + .005 || length < .5 || length > 120) {
        sourceTrackStatus.textContent = "Set a range of 0.5 seconds to 2 minutes inside the trim.";
        return;
    }
    currentTarget.disabled = true;
    byId("editor-separated-tracks").hidden = true;
    sourceTrackStatus.textContent = "Separating locally. The first run may download the model.";
    try {
        const result = await api("/api/edit/" + session.path + "/separate-audio", jsonRequest("POST", {
            start: trim.start + at, end: trim.start + end, revision: session.clip.file_revision || "",
        }));
        if (editSession !== session) return;
        session.separatedAudio = {at, end, trimStart: trim.start, ...result};
        addVoiceoverAsset(result.voice);
        addVoiceoverAsset(result.background);
        byId("editor-separated-tracks").hidden = false;
        sourceTrackStatus.textContent = "Both tracks are ready. Pick one to audition and apply.";
    } catch (error) {
        if (editSession === session) sourceTrackStatus.textContent = error.message;
    } finally {
        if (editSession === session) currentTarget.disabled = false;
    }
});

for (const kind of ["voice", "background"]) {
    byId("editor-keep-" + kind).addEventListener("click", () => {
        const result = editSession.separatedAudio;
        if (result.trimStart !== editorRange().start) {
            sourceTrackStatus.textContent = "The trim start changed. Separate the new range to keep the tracks aligned.";
            return;
        }
        byId("editor-audio-at").value = result.at;
        byId("editor-audio-end").value = result.end;
        byId("editor-audio-source-level").value = 0;
        updateRegionLevels();
        selectNewVoiceover(result[kind], false);
        sourceTrackStatus.textContent = "Selected. Play the audio above, then choose Apply audio to range.";
    });
}

byId("editor-cancel-audio").addEventListener("click", () => setAudioRegionDraft());
byId("editor-save-audio").addEventListener("click", () => {
    const {start, end, valid} = editorRange();
    if (!valid) return;
    const region = {
        at: byId("editor-audio-at").valueAsNumber, end: byId("editor-audio-end").valueAsNumber,
        source_level: byId("editor-audio-source-level").valueAsNumber,
        asset: voiceoverMenu.value, level: byId("editor-voice-level").valueAsNumber,
    };
    const regions = [...editSession.audioRegions.filter(item => item !== selectedAudioRegion), region].sort((a, b) => a.at - b.at);
    const problem = regions.length > 12 ? "This clip already has 12 audio ranges." : audioRangeProblem(regions, end - start);
    timedAudioStatus.textContent = problem;
    if (problem) return;
    clearRenderedPreview();
    editSession.audioRegions = regions;
    renderAudioRegions();
    setAudioRegionDraft();
    jumpToEditorCue(region.at);
    updateEditorLevels();
    scheduleEditorSounds();
    timedAudioStatus.textContent = "Audio range applied. Use Play to hear it in context.";
});

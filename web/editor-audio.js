"use strict";

const voiceoverMenu = byId("editor-voiceover");
const voiceAudition = byId("editor-voice-audition");
const timedAudioStatus = byId("editor-audio-status");
const sourceTrackStatus = byId("editor-scan-status");
const trackAudition = byId("editor-track-audition");
const scanAudioButton = byId("editor-scan-audio");
let selectedAudioRegion;
let scanClock;

function setAudioScanRunning(running) {
    clearInterval(scanClock);
    scanAudioButton.disabled = running;
    scanAudioButton.textContent = running ? "Scanning…" : "Scan audio";
    byId("editor-scan-badge").hidden = !running;
    byId("editor-scan-activity").textContent = running ? "Scanning audio" : "";
    if (!running) return;

    const start = performance.now();
    const clock = byId("editor-scan-time");
    const update = () => {
        const seconds = Math.floor((performance.now() - start) / 1000);
        clock.textContent = Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    };
    update();
    scanClock = setInterval(update, 1000);
}

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
    setAudioScanRunning(false);
    scanAudioButton.classList.replace("button-outline", "button-primary");
    byId("editor-apply-track-mix").disabled = false;
    byId("editor-voice-help").textContent = "Uses voices installed on this Windows PC.";
    sourceTrackStatus.textContent = "";
    byId("editor-track-review").hidden = true;
    byId("editor-source-tracks").replaceChildren();
    for (const id of ["editor-scan-settings", "editor-original-audio", "editor-background-music", "editor-timed-audio", "editor-sound-controls"]) {
        byId(id).open = false;
    }
    releaseTrackAudition();
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
    byId("editor-timed-audio").open = true;
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

function releaseTrackAudition() {
    trackAudition.pause();
    trackAudition.hidden = true;
    trackAudition.removeAttribute("src");
    trackAudition.load();
    byId("editor-track-listening").hidden = true;
}

function updateTrackCount(scan) {
    const muted = scan.tracks.filter(track => !track.keep).length;
    byId("editor-track-count").textContent = muted + " of " + scan.tracks.length + " muted";
    byId("editor-keep-all-tracks").hidden = !muted;
    byId("editor-keep-all-tracks").disabled = Boolean(scan.applying);
    byId("editor-apply-track-mix").disabled = Boolean(scan.applying) || (!muted && !editSession.audioRegions.some(region =>
        region.trackMix && Math.abs(region.at - scan.at) < .002 && Math.abs(region.end - scan.end) < .002));
}

function renderDetectedTracks(scan) {
    const rows = scan.tracks.map(track => {
        const row = element("div", "editor-source-track");
        const name = element("input");
        name.type = "text";
        name.maxLength = 48;
        name.value = track.name ?? track.label;
        name.setAttribute("aria-label", "Rename " + track.label);
        const listen = action("Listen", () => {
            editorVideo.pause();
            renderedVideo.pause();
            voiceAudition.pause();
            trackAudition.hidden = false;
            trackAudition.src = "/api/audio-tracks/" + scan.id + "/" + track.id;
            trackAudition.currentTime = track.first;
            const status = byId("editor-track-listening");
            status.hidden = false;
            status.textContent = "Listening to " + (name.value.trim() || track.label);
            trackAudition.play().catch(error => {
                if (error.name !== "AbortError") status.textContent = "Press play to audition this track.";
            });
        }, null, "button-outline");
        const toggle = action("Mute", () => {
            if (scan.applying) return;
            track.keep = !track.keep;
            updateToggle();
            updateTrackCount(scan);
        }, null, "button-outline");
        toggle.classList.add("editor-track-toggle");
        function updateToggle() {
            const label = name.value.trim() || track.label;
            row.dataset.muted = String(!track.keep);
            toggle.textContent = track.keep ? "Mute" : "Keep";
            toggle.setAttribute("aria-label", (track.keep ? "Mute " : "Keep ") + label);
            listen.setAttribute("aria-label", "Listen to " + label);
        }
        name.addEventListener("input", event => {
            event.stopPropagation();
            track.name = name.value;
            updateToggle();
        });
        updateToggle();
        row.append(name, listen, toggle);
        return row;
    });
    const grouped = [];
    for (const [kind, heading] of [["voice", "Voices"], ["sound", "Sounds and background"]]) {
        const matching = rows.filter((_, index) => scan.tracks[index].kind === kind);
        if (matching.length) grouped.push(element("h4", "editor-track-group", heading), ...matching);
    }
    byId("editor-source-tracks").replaceChildren(...grouped);
    byId("editor-track-review").hidden = !rows.length;
    if (rows.length) byId("editor-scan-audio").classList.replace("button-primary", "button-outline");
    updateTrackCount(scan);
}

byId("editor-keep-all-tracks").addEventListener("click", () => {
    const scan = editSession.audioTrackScan;
    for (const track of scan.tracks) track.keep = true;
    renderDetectedTracks(scan);
    const apply = byId("editor-apply-track-mix");
    (apply.disabled ? byId("editor-scan-audio") : apply).focus();
});

scanAudioButton.addEventListener("click", async () => {
    const session = editSession;
    const trim = editorRange();
    const at = byId("editor-scan-start").valueAsNumber;
    const endInput = byId("editor-scan-end");
    const end = endInput.value ? endInput.valueAsNumber : trim.end - trim.start;
    const length = end - at;
    if (!trim.valid || !Number.isFinite(length) || at < 0 || end > trim.end - trim.start + .005 || length < .5 || length > 120) {
        sourceTrackStatus.textContent = "Set a range of 0.5 seconds to 2 minutes inside the trim.";
        byId("editor-scan-settings").open = true;
        return;
    }
    setAudioScanRunning(true);
    byId("editor-track-review").hidden = true;
    releaseTrackAudition();
    sourceTrackStatus.textContent = "Detecting speakers and sounds on your PC.";
    try {
        const result = await api("/api/edit/" + session.path + "/audio-tracks", jsonRequest("POST", {
            start: trim.start + at, end: trim.start + end, revision: session.clip.file_revision || "",
            speakers: Number(byId("editor-speakers-expected").value),
        }));
        if (editSession !== session) return;
        const previous = session.audioTrackScan?.id === result.id ? session.audioTrackScan.tracks : [];
        const tracks = result.tracks.map(track => previous.find(saved => saved.id === track.id) || {...track, keep: true});
        session.audioTrackScan = {id: result.id, at, end, trimStart: trim.start, tracks};
        renderDetectedTracks(session.audioTrackScan);
        sourceTrackStatus.textContent = result.tracks.length
            ? "Tracks ready. Listen first, then mute the tracks you want removed."
            : "No audible tracks were detected in this range.";
    } catch (error) {
        if (editSession === session) {
            if (session.audioTrackScan) renderDetectedTracks(session.audioTrackScan);
            sourceTrackStatus.textContent = error.message === "Not Found"
                ? "This studio server is missing the audio scan. Restart it with start_web.bat, then reopen this clip."
                : error.message;
        }
    } finally {
        if (editSession === session) setAudioScanRunning(false);
    }
});

byId("editor-apply-track-mix").addEventListener("click", async () => {
    const session = editSession;
    const scan = session.audioTrackScan;
    const trim = editorRange();
    if (!scan || !trim.valid || trim.start !== scan.trimStart || scan.end > trim.end - trim.start + .005) {
        sourceTrackStatus.textContent = "The trim changed. Scan the new range to keep audio aligned.";
        return;
    }
    const keep = scan.tracks.filter(track => track.keep).map(track => track.id);
    const keepAll = keep.length === scan.tracks.length;
    const previousRegions = session.audioRegions;
    const unchanged = session.audioRegions.filter(region =>
        !(region.trackMix && Math.abs(region.at - scan.at) < .002 && Math.abs(region.end - scan.end) < .002));
    const entry = {at: scan.at, end: scan.end, source_level: 0, level: 100, asset: "", trackMix: true};
    const regions = keepAll ? unchanged : [...unchanged, entry].sort((left, right) => left.at - right.at);
    const error = audioRangeProblem(regions, trim.end - trim.start);
    if (error || regions.length > 12) {
        sourceTrackStatus.textContent = error || "This clip already has 12 audio ranges.";
        return;
    }
    scan.applying = true;
    updateTrackCount(scan);
    try {
        if (!keepAll && keep.length) {
            sourceTrackStatus.textContent = "Mixing the tracks marked Keep…";
            const asset = await api("/api/audio-tracks/" + scan.id + "/mix", jsonRequest("POST", {keep}));
            if (editSession !== session || session.audioTrackScan !== scan) return;
            const current = editorRange();
            if (!current.valid || current.start !== trim.start || current.end !== trim.end || session.audioRegions !== previousRegions) {
                sourceTrackStatus.textContent = "Audio or trim changed while mixing. Apply again to use the current settings.";
                return;
            }
            addVoiceoverAsset(asset);
            entry.asset = asset.id;
        }
        releaseTrackAudition();
        clearRenderedPreview();
        session.audioRegions = regions;
        renderAudioRegions();
        jumpToEditorCue(scan.at);
        updateEditorLevels();
        sourceTrackStatus.textContent = keepAll ? "Original mix restored for this range." : "Track changes applied. Play the edited clip to review.";
    } catch (error) {
        if (editSession === session) sourceTrackStatus.textContent = error.message;
    } finally {
        scan.applying = false;
        if (editSession === session && session.audioTrackScan === scan) updateTrackCount(scan);
    }
});

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

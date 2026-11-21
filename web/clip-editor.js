"use strict";

const editorDialog = byId("clip-editor");
const editorForm = byId("editor-form");
const editorVideo = byId("editor-video");
const editorMusic = new Audio();
editorMusic.loop = true;
let editSession;
let editorAudio;
let cueSchedule = 0;
let cueVoices = [];
let auditionVoice;
let previewFrame;
const soundBuffers = new Map();
const editorPlay = byId("editor-play");
const editorSeek = byId("editor-seek");
const editorExport = byId("editor-export");

function editorRange() {
    const start = byId("editor-start").valueAsNumber;
    const end = byId("editor-end").valueAsNumber;
    const startValid = Number.isFinite(start) && start >= 0;
    const valid = startValid && end - start >= .24 && end <= editorVideo.duration + .02;
    return {start, end, valid};
}

function editorClock(seconds) {
    const tenths = Math.max(0, Math.floor((seconds || 0) * 10));
    const whole = Math.floor(tenths / 10);
    return formatTime(whole) + "." + tenths % 10;
}

function updateEditorTransport() {
    const {start, end, valid} = editorRange();
    const length = valid ? end - start : 0;
    const elapsed = valid ? Math.min(length, Math.max(0, editorVideo.currentTime - start)) : 0;
    editorSeek.max = length || 1;
    editorSeek.value = elapsed;
    byId("editor-time").textContent = editorClock(elapsed) + " / " + editorClock(length);
    editorSeek.disabled = editorPlay.disabled = !valid;
    byId("editor-prev-frame").disabled = byId("editor-next-frame").disabled = !valid;
    byId("editor-focus-toggle").disabled = !valid;
    editorExport.disabled = !valid || Boolean(editSession?.saving);
    byId("editor-add-effect").disabled = !valid || !byId("editor-effect").value || (editSession?.cues.length || 0) >= 12;
    updateEditorComparison(start, elapsed, valid);
    updateEditorVisuals(start, length, valid);
}

function updateEditorPicture() {
    const picture = activeEditorPicture();
    const zoom = picture.zoom / 100;
    const crop = focusCrop(picture);
    const x = (50 - crop.x) * zoom;
    const y = (50 - crop.y) * zoom;
    const look = picture.look;
    editorVideo.style.filter = editSession?.looks.get(look) || ({vivid: "saturate(1.25)", monochrome: "grayscale(1)"})[look] || "none";
    editorVideo.style.transform = `translate(${x}%, ${y}%) scale(${zoom}) scaleX(${picture.mirror ? -1 : 1})`;
    updateFocusGuide(crop);
    showEditorFrame(picture.frame);
    byId("editor-zoom-value").textContent = byId("editor-zoom").value + "%";
}

async function enableEditorAudio() {
    if (!editorAudio) {
        const context = new AudioContext();
        const source = context.createGain();
        const music = context.createGain();
        const limiter = context.createDynamicsCompressor();
        limiter.threshold.value = -1;
        limiter.knee.value = 0;
        limiter.ratio.value = 20;
        limiter.connect(context.destination);
        context.createMediaElementSource(editorVideo).connect(source).connect(limiter);
        context.createMediaElementSource(editorMusic).connect(music).connect(limiter);
        editorAudio = {context, source, music, limiter};
        updateEditorLevels();
    }
    await editorAudio.context.resume();
}

function updateEditorLevels() {
    const source = Number(byId("editor-source-level").value);
    const music = Number(byId("editor-music-level").value);
    byId("editor-source-level-value").textContent = source + "%";
    byId("editor-music-level-value").textContent = music + "%";
    if (editorAudio) {
        editorAudio.source.gain.value = source / 100;
        editorAudio.music.gain.value = music / 100;
    }
}

function stopCues() {
    cueSchedule++;
    for (const node of cueVoices) node.stop();
    cueVoices = [];
}

function getEffectBuffer(effect) {
    if (!soundBuffers.has(effect)) {
        const pending = fetch("/api/editor-effects/" + encodeURIComponent(effect)).then(response => {
            if (!response.ok) throw new Error("Could not load the sound effect.");
            return response.arrayBuffer();
        }).then(bytes => editorAudio.context.decodeAudioData(bytes)).catch(error => {
            soundBuffers.delete(effect);
            throw error;
        });
        soundBuffers.set(effect, pending);
    }
    return soundBuffers.get(effect);
}

function playEditorSound(buffer, level, when, offset = 0, duration = buffer.duration - offset) {
    const {context, limiter} = editorAudio;
    const voice = context.createBufferSource();
    const gain = context.createGain();
    gain.gain.value = level / 100;
    voice.buffer = buffer;
    voice.connect(gain).connect(limiter);
    voice.onended = () => {
        if (voice === auditionVoice) auditionVoice = undefined;
        cueVoices = cueVoices.filter(node => node !== voice);
        voice.disconnect();
        gain.disconnect();
    };
    voice.start(when, offset, duration);
    return voice;
}

function editorIsPlaying() {
    return editorDialog.open && !editorVideo.paused && !editorVideo.seeking && editorVideo.readyState >= 3;
}

async function scheduleEditorSounds() {
    stopCues();
    const {start, end, valid} = editorRange();
    if (!valid || !editorAudio || !editorIsPlaying()) return;
    const schedule = cueSchedule;
    const cues = editSession.cues.filter(cue => cue.level && cue.at >= 0 && cue.at < end - start);
    try {
        const buffers = await Promise.all(cues.map(cue => getEffectBuffer(cue.effect)));
        if (schedule !== cueSchedule || !editorIsPlaying()) return;
        const elapsed = editorVideo.currentTime - start;
        const now = editorAudio.context.currentTime;
        for (const [index, cue] of cues.entries()) {
            const delay = cue.at - elapsed;
            const offset = Math.max(0, -delay);
            const duration = Math.min(buffers[index].duration - offset, end - start - Math.max(cue.at, elapsed));
            if (duration > 0) cueVoices.push(playEditorSound(buffers[index], cue.level, now + Math.max(0, delay), offset, duration));
        }
    } catch (error) {
        if (schedule === cueSchedule) message("editor-status", error.message);
    }
}

function syncEditorMusic() {
    if (!editorIsPlaying() || !Number.isFinite(editorMusic.duration) || !editorMusic.duration) return;
    const elapsed = Math.max(0, editorVideo.currentTime - editorRange().start);
    const position = elapsed % editorMusic.duration;
    if (Math.abs(editorMusic.currentTime - position) > .15 || editorMusic.paused) editorMusic.currentTime = position;
    editorMusic.play().catch(() => { if (editorIsPlaying()) message("editor-status", "The browser could not preview this music track."); });
}

function stopEditorMix() {
    editorMusic.pause();
    stopCues();
}

function tickEditorPreview() {
    cancelAnimationFrame(previewFrame);
    updateEditorTransport();
    if (!editorIsPlaying()) return;
    const {end, valid} = editorRange();
    if (!valid || editorVideo.currentTime >= end) {
        editorVideo.pause();
        if (valid) editorVideo.currentTime = end;
        return;
    }
    previewFrame = requestAnimationFrame(tickEditorPreview);
}

function renderEditorCues() {
    const list = byId("editor-cues");
    list.replaceChildren(...editSession.cues.map(cue => {
        const row = element("div", "editor-cue");
        const time = element("input");
        time.type = "number";
        time.min = "0";
        time.step = ".01";
        time.value = cue.at.toFixed(2);
        time.setAttribute("aria-label", cue.label + " time in seconds");
        time.addEventListener("input", () => { cue.at = time.valueAsNumber; scheduleEditorSounds(); });
        const level = element("input");
        level.type = "range";
        level.min = "0";
        level.max = "100";
        level.value = cue.level;
        level.setAttribute("aria-label", cue.label + " volume");
        const percent = element("span", "editor-cue-percent", cue.level + "%");
        level.addEventListener("input", () => {
            cue.level = level.valueAsNumber;
            percent.textContent = cue.level + "%";
            scheduleEditorSounds();
        });
        const remove = action("", () => {
            editSession.cues.splice(editSession.cues.indexOf(cue), 1);
            renderEditorCues();
            byId("editor-add-effect").focus();
            scheduleEditorSounds();
        }, "close");
        remove.setAttribute("aria-label", "Remove " + cue.label);
        row.append(element("strong", null, cue.label), time, level, percent, remove);
        return row;
    }));
    updateEditorTransport();
}

function changeEditorTrack() {
    const filename = byId("editor-track").value;
    editorMusic.pause();
    if (filename) editorMusic.src = "/api/editor-music/" + encodeURIComponent(filename);
    else editorMusic.removeAttribute("src");
    editorMusic.load();
}

async function openClipEditor(clip, path, uri) {
    const session = editSession = {path, clip, cues: [], titles: [], inserts: [], captions: [], ranges: [], fps: 30, looks: new Map(), insertAssets: new Map()};
    editorForm.reset();
    resetEditorVisuals();
    openEditorComparison(uri, session);
    byId("editor-track").replaceChildren(new Option("No music", ""));
    byId("editor-effect").replaceChildren(new Option("Loading sounds…", ""));
    byId("editor-hear-effect").disabled = true;
    editorPlay.textContent = "Play";
    byId("editor-source").textContent = clip.title + " · " + clip.filename;
    const note = byId("editor-sound-note");
    note.hidden = !clip.sound_effect_prompt;
    note.textContent = clip.sound_effect_prompt ? "Posting kit suggestion: " + clip.sound_effect_prompt : "";
    message("editor-status");
    editorVideo.src = uri;
    editorDialog.showModal();
    updateEditorPicture();
    updateEditorLevels();
    renderEditorCues();
    try {
        const assets = await api("/api/editor-assets");
        if (editSession !== session) return;
        for (const track of assets.music) byId("editor-track").add(new Option(track, track));
        byId("editor-effect").replaceChildren(...assets.effects.map(effect => new Option(effect.label, effect.id)));
        byId("editor-hear-effect").disabled = assets.effects.length === 0;
        loadEditorVisualAssets(assets);
        updateEditorTransport();
    } catch (error) {
        if (editSession === session) message("editor-status", error.message);
    }
}

editorVideo.addEventListener("loadedmetadata", () => {
    if (!editorDialog.open) return;
    byId("editor-end").value = editorVideo.duration.toFixed(2);
    const aspect = editorVideo.videoWidth / editorVideo.videoHeight;
    for (const frame of editorDialog.querySelectorAll(".editor-frame")) {
        frame.style.aspectRatio = String(aspect);
        frame.style.setProperty("--preview-max-width", "min(" + Math.min(85, 49 * aspect) + "vh, 460px)");
    }
    updateEditorTransport();
    renderEditorTimeline();
});
editorVideo.addEventListener("error", () => {
    if (editorDialog.open) message("editor-status", "Could not load this video for preview.");
});
editorVideo.addEventListener("playing", () => {
    editorPlay.textContent = "Pause";
    syncEditorMusic();
    scheduleEditorSounds();
    tickEditorPreview();
});
editorVideo.addEventListener("seeked", () => {
    syncEditorMusic();
    scheduleEditorSounds();
    tickEditorPreview();
});
editorVideo.addEventListener("pause", () => {
    editorPlay.textContent = "Play";
    stopEditorMix();
    tickEditorPreview();
});
editorVideo.addEventListener("seeking", stopEditorMix);
editorVideo.addEventListener("waiting", stopEditorMix);
editorMusic.addEventListener("loadedmetadata", syncEditorMusic);

editorPlay.addEventListener("click", async () => {
    if (!editorVideo.paused) {
        editorVideo.pause();
        return;
    }
    const {start, end, valid} = editorRange();
    if (!valid) return;
    const session = editSession;
    try {
        await enableEditorAudio();
        if (editSession !== session) return;
        if (editorVideo.currentTime < start || editorVideo.currentTime >= end - .01) editorVideo.currentTime = start;
        await editorVideo.play();
    } catch (error) {
        if (editSession === session) message("editor-status", error.message);
    }
});
editorSeek.addEventListener("input", () => { editorVideo.currentTime = editorRange().start + editorSeek.valueAsNumber; });
editorForm.addEventListener("input", ({target}) => {
    if (["look", "zoom", "mirror", "frame"].includes(target.name)) updateEditorPicture();
    if (["source_level", "music_level"].includes(target.name)) updateEditorLevels();
});
for (const id of ["editor-start", "editor-end"]) {
    byId(id).addEventListener("input", () => {
        editorVideo.pause();
        const {start, end, valid} = editorRange();
        if (valid && (id === "editor-start" || editorVideo.currentTime >= end)) editorVideo.currentTime = start;
        updateEditorTransport();
        renderEditorTimeline();
    });
}

byId("editor-track").addEventListener("change", changeEditorTrack);
byId("editor-add-track").addEventListener("click", () => byId("editor-track-file").click());
byId("editor-track-file").addEventListener("change", async ({target}) => {
    const file = target.files[0];
    if (!file) return;
    const session = editSession;
    try {
        const {filename} = await api("/api/music", fileRequest(file));
        if (session !== editSession) return;
        byId("editor-track").add(new Option(filename, filename, false, true));
        changeEditorTrack();
        message("editor-status", "Track added to your local library.", true);
    } catch (error) {
        if (session === editSession) message("editor-status", error.message);
    } finally {
        target.value = "";
    }
});

byId("editor-hear-effect").addEventListener("click", async () => {
    const session = editSession;
    const effect = byId("editor-effect").value;
    if (!effect) return;
    try {
        await enableEditorAudio();
        const buffer = await getEffectBuffer(effect);
        if (editSession !== session) return;
        auditionVoice?.stop();
        auditionVoice = playEditorSound(buffer, 65, editorAudio.context.currentTime);
    } catch (error) {
        if (editSession === session) message("editor-status", error.message);
    }
});
byId("editor-add-effect").addEventListener("click", () => {
    const {start, end, valid} = editorRange();
    const selection = byId("editor-effect");
    if (!valid || !selection.value || editSession.cues.length >= 12) return;
    const at = Math.max(0, Math.min(end - start - .02, editorVideo.currentTime - start));
    editSession.cues.push({effect: selection.value, label: selection.selectedOptions[0].text, at: Number(at.toFixed(2)), level: 45});
    renderEditorCues();
    scheduleEditorSounds();
});

async function submitEditorExport(session, edit) {
    session.saving = true;
    updateEditorTransport();
    try {
        const job = await api("/api/edit/" + session.path, jsonRequest("POST", edit));
        if (editSession === session) editorDialog.close();
        toast("Edited clip queued · #" + job.job_id);
        trackSubmittedJob(job.job_id);
    } catch (error) {
        if (editSession === session) message("editor-status", error.message);
    } finally {
        session.saving = false;
        if (editSession === session) updateEditorTransport();
    }
}

function editorPayload() {
    const session = editSession;
    const {start, end, valid} = editorRange();
    if (!session || !valid || session.saving) return null;
    if (session.cues.some(cue => !Number.isFinite(cue.at) || cue.at < 0 || cue.at >= end - start)) {
        message("editor-status", "Move every sound effect inside the trim range.");
        return null;
    }
    const problem = validateEditorVisuals(session, end - start);
    if (problem) {
        message("editor-status", problem);
        return null;
    }
    const fields = new FormData(editorForm);
    return {
        start, end, zoom: Number(fields.get("zoom")), mirror: fields.has("mirror"), look: fields.get("look"),
        source_level: Number(fields.get("source_level")), music_track: fields.get("music_track"),
        music_level: Number(fields.get("music_level")),
        save_mode: fields.get("save_mode"), source_revision: session.clip.file_revision || "",
        cues: session.cues.map(({effect, at, level}) => ({effect, at, level})),
        frame: fields.get("frame"), titles: session.titles, inserts: session.inserts, captions: session.captions,
        ranges: session.ranges,
    };
}

editorForm.addEventListener("submit", event => {
    event.preventDefault();
    const session = editSession;
    const edit = editorPayload();
    if (!edit) return;
    clearRenderedPreview();
    editorVideo.pause();
    if (edit.save_mode === "replace") {
        const cancel = action("Cancel", () => dialog.close());
        const replace = action("Replace file", () => {
            dialog.close();
            submitEditorExport(session, edit);
        }, null, "button-danger");
        const buttons = element("div", "dialog-actions");
        buttons.append(cancel, replace);
        const detail = element("p", null, "This overwrites " + session.clip.filename + " after rendering succeeds.");
        showDialog("Replace existing clip?", [detail, buttons]);
        cancel.focus();
    } else {
        submitEditorExport(session, edit);
    }
});

editorDialog.addEventListener("close", () => {
    if (editorDialog.open) return;
    clearRenderedPreview();
    closeEditorComparison();
    releaseReactionPreview();
    editSession = undefined;
    cancelAnimationFrame(previewFrame);
    stopEditorMix();
    auditionVoice?.stop();
    editorVideo.pause();
    editorVideo.removeAttribute("src");
    editorVideo.load();
    byId("editor-track").value = "";
    changeEditorTrack();
});

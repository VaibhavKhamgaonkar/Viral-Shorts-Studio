"use strict";

const platformNames = {youtube: "YouTube", instagram: "Instagram", facebook: "Facebook"};
const publishDialog = byId("publish-dialog");
const publishForm = byId("publish-form");
let publication;

function connectionStatus(text, success = false) {
    message("publish-connection-status", text, success);
}

async function loadPublishingSettings() {
    try {
        const {accounts} = await api("/api/publishing");
        for (const platform of destinations) {
            byId(platform + "-connected").textContent = accounts[platform]?.name || "Connect an account";
            byId(platform + "-enabled").disabled = !accounts[platform];
        }
        byId("disconnect-youtube").hidden = !accounts.youtube;
        byId("disconnect-meta").hidden = !accounts.instagram && !accounts.facebook;
        const query = new URLSearchParams(location.search);
        if (query.has("publishing_connected")) connectionStatus("YouTube connected.", true);
        if (query.has("publishing_error")) connectionStatus(query.get("publishing_error"));
        if (query.has("publishing_connected") || query.has("publishing_error")) {
            history.replaceState(null, "", "/settings#publishing");
        }
    } catch (error) {
        connectionStatus(error.message);
    }
}

if (route === "settings") loadPublishingSettings();

byId("connect-youtube").addEventListener("click", async ({currentTarget: button}) => {
    button.disabled = true;
    try {
        const file = byId("youtube-client-file").files[0];
        if (!file || file.size > 65536) throw new Error("Choose the desktop OAuth client JSON from Google Cloud.");
        const {installed} = JSON.parse(await file.text());
        if (!installed?.client_id || !installed?.client_secret) throw new Error("Choose a Google Desktop app client JSON.");
        const result = await api("/api/publishing/youtube/connect", jsonRequest("POST", {
            client_id: installed.client_id, client_secret: installed.client_secret,
        }));
        location.assign(result.authorization_url);
    } catch (error) {
        connectionStatus(error instanceof SyntaxError ? "The Google OAuth client file is invalid." : error.message);
        button.disabled = false;
    }
});

byId("connect-meta").addEventListener("click", async ({currentTarget: button}) => {
    const token = byId("meta-user-token");
    button.disabled = true;
    connectionStatus("Checking your Meta Page and permissions…");
    try {
        const {accounts} = await api("/api/publishing/meta/connect", jsonRequest("POST", {
            page_id: byId("meta-page-id").value.trim(), user_token: token.value.trim(),
        }));
        await loadPublishingSettings();
        connectionStatus(destinations.filter(platform => platform !== "youtube" && accounts[platform])
            .map(platform => platformNames[platform]).join(" and ") + " connected.", true);
    } catch (error) {
        connectionStatus(error.message);
    } finally {
        token.value = "";
        button.disabled = false;
    }
});

for (const platform of ["youtube", "meta"]) {
    byId("disconnect-" + platform).addEventListener("click", async ({currentTarget: button}) => {
        button.disabled = true;
        try {
            await api("/api/publishing/" + platform, {method: "DELETE"});
            await loadPublishingSettings();
            connectionStatus("Disconnected from this studio.", true);
        } catch (error) {
            connectionStatus(error.message);
        } finally {
            button.disabled = false;
        }
    });
}

function publishField(name, input) {
    const label = element("label", "field");
    label.append(element("span", null, name), input);
    return label;
}

function publishChoice(label, value, checked) {
    const input = element("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = checked;
    const choice = element("label", "check");
    choice.append(input, document.createTextNode(label));
    return {input, choice};
}

function publishClipRow(clip, single) {
    const row = element("div", "publish-clip-row");
    const {input, choice} = publishChoice(clip.filename, clip.filename, true);
    if (single) input.hidden = true;
    const fields = element("div", "publish-clip-fields");
    const title = element("input");
    title.value = clip.title.slice(0, 100);
    title.maxLength = 100;
    title.required = true;
    const description = element("textarea");
    description.rows = 3;
    description.value = postText(clip);
    description.maxLength = 5000;
    fields.append(publishField("Title", title), publishField("Description and hashtags", description));
    row.append(choice, fields);
    input.addEventListener("change", () => {
        fields.hidden = !input.checked;
        title.disabled = !input.checked;
        description.disabled = !input.checked;
    });
    return {clip, row, input, title, description};
}

function updatePublication() {
    if (publishForm.hidden || !publication) return;
    const selected = publication.targets.filter(({input}) => input.checked).map(({input}) => input.value);
    const youtube = selected.includes("youtube");
    byId("publish-youtube-options").hidden = !youtube;
    byId("publish-audience").required = youtube;
    byId("publish-meta-note").hidden = !selected.some(platform => platform !== "youtube");
    byId("publish-submit").disabled = publication.pending || !selected.length ||
        !publication.clips.some(({input}) => input.checked) || !byId("publish-rights").checked ||
        (youtube && !byId("publish-audience").value);
}

async function openPublication(folder, clips) {
    const state = publication = {folder, pending: false};
    publishForm.reset();
    publishForm.hidden = true;
    byId("publish-submit").disabled = true;
    byId("publish-dialog-settings").hidden = true;
    byId("publish-dialog-loading").hidden = false;
    byId("publish-dialog-loading").textContent = "Checking connected accounts…";
    byId("publish-dialog-status").textContent = "";
    const summary = byId("publish-dialog-summary");
    summary.textContent = clips.length === 1 ? clips[0].title : clips.length + " exported clips";
    publishDialog.showModal();
    publishDialog.querySelector(".kit-scroll").scrollTop = 0;
    try {
        const {accounts, defaults} = await api("/api/publishing");
        if (publication !== state || !publishDialog.open) return;
        const available = destinations.filter(platform => accounts[platform]);
        const hasDefault = available.some(platform => defaults[platform]);
        state.targets = destinations.map(platform => {
            const account = accounts[platform];
            const {input, choice} = publishChoice(platformNames[platform] + " · " + (account?.name || "Connect in Settings"),
                platform, Boolean(account && (hasDefault ? defaults[platform] : available.length === 1)));
            input.disabled = !account;
            return {input, choice, account};
        });
        if (!available.length) {
            byId("publish-dialog-loading").textContent = "Connect at least one account to upload clips.";
            byId("publish-dialog-settings").hidden = false;
            return;
        }
        state.clips = clips.map(clip => publishClipRow(clip, clips.length === 1));
        byId("publish-clips").replaceChildren(...state.clips.map(({row}) => row));
        byId("publish-targets").replaceChildren(...state.targets.map(({choice}) => choice));
        byId("publish-dialog-loading").hidden = true;
        publishForm.hidden = false;
        updatePublication();
    } catch (error) {
        if (publication !== state || !publishDialog.open) return;
        byId("publish-dialog-loading").textContent = error.message === "Not Found"
            ? "Restart the studio with start_web.bat to enable direct uploads." : error.message;
    }
}

publishForm.addEventListener("change", updatePublication);
publishForm.addEventListener("submit", async event => {
    event.preventDefault();
    const state = publication;
    if (state.pending || byId("publish-submit").disabled) return;
    state.pending = true;
    updatePublication();
    byId("publish-dialog-status").textContent = "Queueing uploads…";
    try {
        const selected = Object.fromEntries(state.targets.filter(({input}) => input.checked)
            .map(({input, account}) => [input.value, account.id]));
        const clips = state.clips.filter(({input}) => input.checked).map(({clip, title, description}) => ({
            filename: clip.filename, revision: clip.file_revision, title: title.value.trim(), description: description.value,
        }));
        const audience = byId("publish-audience").value;
        const {uploads} = await api("/api/publishing/batch/" + encodeURIComponent(state.folder), jsonRequest("POST", {
            clips, destinations: selected, visibility: byId("publish-visibility").value,
            made_for_kids: audience === "" ? null : audience === "true",
            synthetic_media: byId("publish-synthetic").checked, rights_confirmed: byId("publish-rights").checked,
        }));
        const active = uploads.filter(upload => upload.job_id);
        const complete = uploads.length - active.length;
        if (publication !== state || !publishDialog.open) return;
        if (!active.length) {
            const status = byId("publish-dialog-status");
            status.textContent = "Already uploaded to the selected destinations.";
            const target = uploads.find(upload => upload.url)?.url;
            if (target && /^https:\/\/(?:studio\.youtube\.com|www\.instagram\.com|www\.facebook\.com)\//.test(target)) {
                const view = element("a", null, " View upload");
                view.href = target;
                view.target = "_blank";
                view.rel = "noopener noreferrer";
                status.append(view);
            }
            return;
        }
        publishDialog.close();
        toast(active.length + (active.length === 1 ? " upload in Tasks." : " uploads in Tasks.")
            + (complete ? " " + complete + " already uploaded." : ""));
        await loadQueue();
        openTasks(active[0].job_id);
    } catch (error) {
        if (publication === state && publishDialog.open) byId("publish-dialog-status").textContent = error.message;
    } finally {
        state.pending = false;
        if (publication === state) updatePublication();
    }
});

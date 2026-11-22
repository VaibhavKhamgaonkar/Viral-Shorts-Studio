"use strict";

const platformNames = {youtube: "YouTube", instagram: "Instagram", facebook: "Facebook"};
const publishDialog = byId("publish-dialog");
const publishForm = byId("publish-form");
let publication;

function connectionStatus(service, text, success = false) {
    message(service + "-connection-status", text, success);
}

async function loadPublishingSettings() {
    try {
        const {accounts, defaults, instagram_setup: instagramSetup} = await api("/api/publishing");
        for (const platform of destinations) {
            const name = byId(platform + "-connected");
            name.textContent = accounts[platform]?.name || "Not connected";
            name.dataset.state = accounts[platform] ? "connected" : "disconnected";
            byId(platform + "-default").hidden = !accounts[platform];
            byId(platform + "-enabled").checked = Boolean(defaults[platform]);
        }
        byId("disconnect-youtube").hidden = !accounts.youtube;
        byId("disconnect-meta").hidden = !accounts.instagram && !accounts.facebook;
        const instagramDetail = byId("instagram-connection-detail");
        instagramDetail.hidden = !accounts.facebook || Boolean(accounts.instagram);
        const instagramMessages = {
            permissions: () => "The last token is missing " + instagramSetup.missing.join(", ") + ". Add the missing permissions and generate a new User Access Token.",
            unlinked: () => "Meta did not return a professional Instagram account for this Page. Check Linked accounts on your Facebook Page.",
            lookup_failed: () => "Meta could not verify the linked Instagram account. Check Linked accounts and reconnect with a fresh token.",
        };
        instagramDetail.textContent = instagramMessages[instagramSetup?.state]?.()
            || "Use Manage Meta connection to link Instagram and check the token permissions.";
        byId("youtube-setup-label").textContent = accounts.youtube ? "Change YouTube account" : "Set up YouTube";
        byId("meta-setup-label").textContent = accounts.facebook || accounts.instagram ? "Manage Meta connection" : "Set up Facebook & Instagram";
        const query = new URLSearchParams(location.search);
        if (query.has("publishing_connected")) connectionStatus("youtube", "Connected to " + accounts.youtube?.name + ".", true);
        if (query.has("publishing_error")) {
            byId("youtube-setup").open = true;
            connectionStatus("youtube", query.get("publishing_error"));
        }
        if (query.has("publishing_connected") || query.has("publishing_error")) {
            history.replaceState(null, "", "/settings#publishing");
        }
    } catch (error) {
        for (const platform of destinations) byId(platform + "-connected").textContent = "Connection unavailable";
        connectionStatus("youtube", publishingSetupError(error));
    }
}

function publishingSetupError(error) {
    return error.message === "Not Found"
        ? "Restart the local studio with start_web.bat, then reload Settings." : error.message;
}

if (route === "settings") {
    loadPublishingSettings();
    const openSetupLink = () => {
        if (location.hash === "#youtube-setup") byId("youtube-setup").open = true;
        if (location.hash === "#meta-setup") byId("meta-setup").open = true;
    };
    openSetupLink();
    window.addEventListener("hashchange", openSetupLink);
}

byId("youtube-connect-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = byId("connect-youtube");
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
        connectionStatus("youtube", error instanceof SyntaxError ? "Choose the JSON file downloaded from Google." : publishingSetupError(error));
        button.disabled = false;
    }
});

const metaToken = byId("meta-user-token");
const metaPage = byId("meta-page-id");
const findMetaPages = byId("load-meta-pages");
const connectMetaPage = byId("connect-meta");

function clearMetaPages() {
    metaPage.replaceChildren();
    metaPage.disabled = true;
    connectMetaPage.disabled = true;
    byId("meta-page-choice").hidden = true;
}

metaToken.addEventListener("input", clearMetaPages);
metaPage.addEventListener("change", () => { connectMetaPage.disabled = !metaPage.value; });

byId("meta-connect-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (findMetaPages.disabled) return;
    const token = metaToken.value.trim();
    clearMetaPages();
    findMetaPages.disabled = true;
    connectionStatus("meta", "Finding your Facebook Pages…");
    try {
        const {pages} = await api("/api/publishing/meta/pages", jsonRequest("POST", {user_token: token}));
        if (metaToken.value.trim() !== token) return;
        metaPage.replaceChildren(new Option("Choose a Page", ""), ...pages.map(page => new Option(page.name, page.id)));
        if (pages.length === 1) metaPage.value = pages[0].id;
        metaPage.disabled = false;
        connectMetaPage.disabled = !metaPage.value;
        byId("meta-page-choice").hidden = false;
        connectionStatus("meta", "Choose a Page and click Connect this Page.");
        metaPage.focus();
    } catch (error) {
        connectionStatus("meta", publishingSetupError(error));
    } finally {
        findMetaPages.disabled = false;
    }
});

connectMetaPage.addEventListener("click", async ({currentTarget: button}) => {
    button.disabled = true;
    findMetaPages.disabled = true;
    metaToken.disabled = true;
    metaPage.disabled = true;
    connectionStatus("meta", "Checking the Page and publishing permissions…");
    try {
        const {accounts} = await api("/api/publishing/meta/connect", jsonRequest("POST", {
            page_id: metaPage.value, user_token: metaToken.value.trim(),
        }));
        metaToken.value = "";
        clearMetaPages();
        await loadPublishingSettings();
        byId("meta-setup").open = false;
        const names = [accounts.facebook?.name, accounts.instagram?.name].filter(Boolean);
        connectionStatus("meta", "Connected to " + names.join(" and ") + ".", true);
    } catch (error) {
        connectionStatus("meta", publishingSetupError(error));
    } finally {
        metaToken.disabled = false;
        findMetaPages.disabled = false;
        metaPage.disabled = !metaPage.options.length;
        button.disabled = !metaPage.value;
    }
});

for (const platform of ["youtube", "meta"]) {
    byId("disconnect-" + platform).addEventListener("click", async ({currentTarget: button}) => {
        button.disabled = true;
        try {
            await api("/api/publishing/" + platform, {method: "DELETE"});
            await loadPublishingSettings();
            connectionStatus(platform, "Disconnected from this studio.", true);
        } catch (error) {
            connectionStatus(platform, publishingSetupError(error));
        } finally {
            button.disabled = false;
        }
    });
}

for (const platform of destinations) {
    byId(platform + "-enabled").addEventListener("change", async ({currentTarget: input}) => {
        const service = platform === "youtube" ? "youtube" : "meta";
        const enabled = input.checked;
        input.disabled = true;
        try {
            await api("/api/publishing/defaults/" + platform + "?enabled=" + enabled, {method: "PUT"});
            connectionStatus(service, platformNames[platform] + (enabled ? " will be selected when you open Share." : " will start unselected when you open Share."), true);
        } catch (error) {
            input.checked = !enabled;
            connectionStatus(service, publishingSetupError(error));
        } finally {
            input.disabled = false;
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
    const feedback = element("p", "kit-muted publish-draft-feedback");
    feedback.setAttribute("role", "status");
    const keep = action("Use current copy", () => {
        entry.useCurrent = true;
        keep.hidden = true;
        feedback.textContent = "Using the current title, description, and hashtags.";
        updatePublication();
    });
    keep.hidden = true;
    const draft = element("div", "publish-draft-state");
    draft.append(feedback, keep);
    row.append(choice, fields, draft);
    const entry = {clip, row, input, title, description, feedback, keep, draft, refreshing: false, useCurrent: false};
    title.addEventListener("input", () => { entry.titleEdited = true; });
    description.addEventListener("input", () => { entry.descriptionEdited = true; });
    input.addEventListener("change", () => {
        fields.hidden = !input.checked;
        draft.hidden = !input.checked;
        title.disabled = !input.checked;
        description.disabled = !input.checked;
    });
    return entry;
}

async function refreshPublicationDrafts(state) {
    for (const entry of state.clips) {
        entry.refreshing = true;
        entry.keep.hidden = false;
        entry.feedback.textContent = "Waiting for a fresh AI draft…";
    }
    updatePublication();
    for (const entry of state.clips) {
        if (publication !== state || !publishDialog.open) break;
        if (entry.useCurrent || !entry.input.checked) {
            entry.refreshing = false;
            entry.keep.hidden = true;
            if (!entry.useCurrent) entry.feedback.textContent = "Draft from the clip library.";
            updatePublication();
            continue;
        }
        entry.feedback.textContent = "Refreshing the title, description, and hashtags…";
        try {
            const refreshed = await api(kitPath({folder: state.folder, clip: entry.clip}), jsonRequest("POST", {
                notes: entry.clip.posting_notes || "", refresh_hashtags: true,
            }));
            Object.assign(entry.clip, refreshed);
            if (publication !== state || !publishDialog.open || entry.useCurrent) continue;
            if (!entry.titleEdited) entry.title.value = refreshed.title.slice(0, 100);
            if (!entry.descriptionEdited) entry.description.value = postText(entry.clip);
            entry.feedback.textContent = entry.titleEdited || entry.descriptionEdited
                ? "Fresh draft ready. Your edits were kept." : "Fresh AI draft ready to review.";
        } catch (error) {
            if (publication === state && publishDialog.open && !entry.useCurrent) {
                entry.feedback.textContent = error.message + " You can edit or use the current copy.";
            }
        } finally {
            entry.refreshing = false;
            entry.keep.hidden = true;
            if (publication === state && publishDialog.open) updatePublication();
        }
    }
}

function updatePublication() {
    if (publishForm.hidden || !publication) return;
    const selected = publication.targets.filter(({input}) => input.checked).map(({input}) => input.value);
    const youtube = selected.includes("youtube");
    byId("publish-youtube-options").hidden = !youtube;
    byId("publish-audience").required = youtube;
    const facebook = selected.includes("facebook");
    const instagram = selected.includes("instagram");
    const note = byId("publish-meta-note");
    note.hidden = !facebook && !instagram;
    note.textContent = [
        facebook && "Facebook posts are public. Clips outside 3–90 seconds are sent as Page videos.",
        instagram && "Instagram Reels use the connected account’s visibility.",
    ].filter(Boolean).join(" ");
    const awaitingDraft = publication.clips.some(entry => entry.input.checked && entry.refreshing && !entry.useCurrent);
    byId("publish-submit").disabled = publication.pending || !selected.length ||
        !publication.clips.some(({input}) => input.checked) || !byId("publish-rights").checked ||
        awaitingDraft || (youtube && !byId("publish-audience").value);
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
        state.targets = destinations.map(platform => {
            const account = accounts[platform];
            const {input, choice} = publishChoice(platformNames[platform] + " · " + (account?.name || "Connect in Settings"),
                platform, Boolean(account && (defaults[platform] ?? available.length === 1)));
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
        byId("publish-dialog-settings").hidden = available.length === destinations.length;
        publishForm.hidden = false;
        void refreshPublicationDrafts(state);
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

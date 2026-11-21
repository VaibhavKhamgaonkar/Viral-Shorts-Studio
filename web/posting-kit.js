"use strict";

const kitDialog = byId("posting-kit");
let activeKit;

function kitStatus(text, error = false) {
    const status = byId("kit-status");
    status.textContent = text;
    status.classList.toggle("kit-error", error);
}

async function copyKitText(text, label) {
    try {
        await navigator.clipboard.writeText(text);
        kitStatus(label + " copied.");
    } catch {
        kitStatus("Clipboard unavailable. Select the text and copy it.", true);
    }
}

function kitSection(title, className = "") {
    const section = element("section", "kit-section " + className);
    section.append(element("h3", null, title));
    return section;
}

function kitCopyButton(label, text) {
    return action("Copy " + label.toLowerCase(), () => copyKitText(text, label), "copy");
}

function kitTags(clip) {
    const tags = Array.isArray(clip.tags) ? clip.tags : [];
    return [...new Set(tags.filter(tag => typeof tag === "string" && tag.trim())
        .map(tag => "#" + tag.replace(/^#+/, "").replace(/\s+/g, "")).filter(tag => tag.length > 1))];
}

function postText(clip) {
    return [clip.description, kitTags(clip).join(" ")].filter(Boolean).join("\n\n");
}

function titlePicker(clip) {
    const group = element("fieldset", "kit-titles");
    group.append(element("legend", null, "Choose your title"));
    for (const [index, title] of [clip.title, ...(clip.title_variants || [])].entries()) {
        if (!title) continue;
        const option = element("label", "kit-title-choice");
        const radio = element("input");
        radio.type = "radio";
        radio.name = "kit-selected-title";
        radio.value = title;
        radio.checked = index === 0;
        const copy = element("span");
        copy.append(element("strong", null, title));
        if (!index) copy.append(element("small", null, "Recommended"));
        option.append(radio, copy);
        group.append(option);
    }
    group.append(action("Copy title", () => copyKitText(chosenTitle(), "Title"), "copy"));
    return group;
}

function chosenTitle() {
    return kitDialog.querySelector('input[name="kit-selected-title"]:checked')?.value || activeKit.clip.title;
}

function renderKit(clip) {
    const content = byId("kit-draft");
    byId("kit-context").textContent = clip.copy_model ? "Drafted by " + clip.copy_model : "Publish your clip";
    const available = clip.copy_source === "ai";
    byId("kit-copy-post").hidden = !available || !clip.description;
    if (!available) {
        const empty = kitSection("No draft yet", "kit-empty");
        empty.append(element("p", null, "Add a note if the scene carries the story, then generate a draft with your local model."));
        content.replaceChildren(empty);
        return;
    }

    const sections = [];
    if (clip.copy_needs_review) {
        sections.push(element("p", "kit-review", "This draft was written before the latest edit. Review it against the new clip or regenerate it."));
    } else if (!clip.posting_kit_version || !clip.copy_ready) {
        const note = clip.posting_kit_version
            ? "This draft is missing some fields. Add clip context and regenerate to fill them in."
            : "This kit uses the earlier format. Regenerate for a fuller caption, topical hashtags, and engagement prompts.";
        sections.push(element("p", "kit-review", note));
    }

    const main = element("div", "kit-main");
    const support = element("div", "kit-support");
    main.append(titlePicker(clip));
    const caption = kitSection("Caption");
    if (clip.description) {
        caption.append(element("p", "kit-caption", clip.description),
            kitCopyButton(kitTags(clip).length ? "Caption + hashtags" : "Caption", postText(clip)));
    } else {
        caption.append(element("p", "kit-muted", "No caption in this draft."));
    }
    main.append(caption);

    const hook = kitSection("Opening hook", "kit-hook");
    hook.append(element("p", "kit-hook-text", clip.ai_hook || "No hook in this draft."));
    if (clip.ai_hook) hook.append(kitCopyButton("Hook", clip.ai_hook));
    support.append(hook);

    const hashtags = kitSection("Hashtags");
    const tags = kitTags(clip);
    if (tags.length) {
        const list = element("ul", "kit-tags");
        list.setAttribute("aria-label", "Suggested hashtags");
        list.append(...tags.map(tag => element("li", null, tag)));
        hashtags.append(list, kitCopyButton("Hashtags", tags.join(" ")));
    } else {
        hashtags.append(element("p", "kit-muted", "No hashtags in this draft."));
    }
    support.append(hashtags);

    const comment = kitSection("Pinned comment");
    comment.append(element("p", null, clip.pinned_comment || "No comment prompt in this draft."));
    if (clip.pinned_comment) comment.append(kitCopyButton("Pinned comment", clip.pinned_comment));
    support.append(comment);
    const grid = element("div", "kit-grid");
    grid.append(main, support);
    sections.push(grid);

    const guidance = [
        ["Who it’s for", clip.audience], ["How to frame the post", clip.posting_tip],
        ["Editing suggestion", clip.editing_tip], ["Grounding phrase", clip.evidence],
        ["Music brief", clip.music_brief], ["Sound-effect suggestion", clip.sound_effect_prompt],
        ["Suggested library track", clip.music_track], ["Audio used in export", clip.applied_music],
    ].filter(([, value]) => value);
    if (guidance.length) {
        const details = element("details", "kit-guidance");
        details.append(element("summary", null, "Audience, editing & audio notes"));
        const list = element("dl");
        for (const [label, value] of guidance) list.append(element("dt", null, label), element("dd", null, value));
        details.append(list);
        sections.push(details);
    }
    content.replaceChildren(...sections);
}

function openPostingKit(clip, folder, trigger) {
    activeKit = {clip, folder, trigger};
    byId("kit-notes-input").value = clip.posting_notes || "";
    byId("kit-notes").open = !clip.copy_ready;
    byId("kit-refresh").disabled = false;
    byId("kit-refresh").querySelector("span").textContent = clip.copy_source === "ai" ? "Regenerate draft" : "Generate draft";
    kitStatus("");
    renderKit(clip);
    kitDialog.showModal();
    kitDialog.querySelector(".kit-scroll").scrollTop = 0;
}

byId("kit-copy-post").addEventListener("click", () => {
    const {clip} = activeKit;
    copyKitText([chosenTitle(), postText(clip)].filter(Boolean).join("\n\n"), "Post");
});

byId("kit-refresh").addEventListener("click", async () => {
    const request = activeKit;
    const button = byId("kit-refresh");
    button.disabled = true;
    kitStatus("Generating with the local model…");
    try {
        const path = "/api/posting-kit/" + encodeURIComponent(request.folder) + "/" + encodeURIComponent(request.clip.filename);
        const refreshed = await api(path, jsonRequest("POST", {notes: byId("kit-notes-input").value.trim()}));
        Object.assign(request.clip, refreshed);
        const card = request.trigger.closest(".clip");
        if (card) {
            card.querySelector(".clip-title").textContent = refreshed.title;
            card.querySelector("video").setAttribute("aria-label", refreshed.title);
        }
        if (activeKit === request && kitDialog.open) {
            renderKit(request.clip);
            button.querySelector("span").textContent = "Regenerate draft";
            kitStatus("New draft saved.");
        }
    } catch (error) {
        if (activeKit === request && kitDialog.open) kitStatus(error.message, true);
    } finally {
        if (activeKit === request) button.disabled = false;
    }
});

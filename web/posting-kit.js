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
    const suggestions = Array.isArray(clip.tags) ? clip.tags : [];
    const tags = new Map();
    for (const value of [...youtubeTagSources(clip).map(entry => entry.tag), ...suggestions]) {
        if (typeof value !== "string") continue;
        const tag = "#" + value.trim().replace(/^#+/, "").replace(/\s+/g, "");
        if (tag.length > 1 && !tags.has(tag.toLowerCase())) tags.set(tag.toLowerCase(), tag);
    }
    return [...tags.values()].slice(0, 5);
}

function youtubeTagSources(clip) {
    const research = clip.hashtag_research;
    return research?.platform === "youtube" && Array.isArray(research.entries) ? research.entries : [];
}

function kitPath(request) {
    return "/api/posting-kit/" + encodeURIComponent(request.folder) + "/" + encodeURIComponent(request.clip.filename);
}

function hashtagSection(clip) {
    const section = kitSection("Hashtags", "kit-hashtags");
    const tags = kitTags(clip);
    const sources = new Map(youtubeTagSources(clip).map(entry => ["#" + entry.tag.toLowerCase(), entry]));
    if (tags.length) {
        const list = element("ul", "kit-tags");
        list.setAttribute("aria-label", "Hashtags included when you copy the post");
        for (const tag of tags) {
            const source = sources.get(tag.toLowerCase());
            const item = element("li");
            if (source && /^[A-Za-z0-9_-]{11}$/.test(source.video_id)) {
                const evidence = element("a", null, tag);
                evidence.href = "https://www.youtube.com/watch?v=" + source.video_id;
                evidence.target = "_blank";
                evidence.rel = "noopener noreferrer";
                const label = tag + " on YouTube" + (Number.isFinite(source.views) ? " · " + source.views.toLocaleString() + " views on this video" : "");
                evidence.setAttribute("aria-label", label + " (opens in a new tab)");
                evidence.title = label;
                evidence.append(icon("link"));
                item.className = "kit-tag-sourced";
                item.append(evidence);
            } else {
                item.append(element("span", null, tag));
            }
            list.append(item);
        }
        section.append(list);
        if (sources.size) {
            const research = clip.hashtag_research;
            const checked = new Date(research.checked_at);
            const date = Number.isNaN(checked.getTime()) ? "" : " · " + checked.toLocaleDateString();
            section.append(element("p", "kit-muted",
                "YouTube sample of " + research.sample_size + " short videos for “" + research.topic + "”" + date + "."));
        }
        if (tags.length > sources.size) section.append(element("p", "kit-muted", "Unlinked tags are AI suggestions."));
        section.append(kitCopyButton("Hashtags", tags.join(" ")));
    } else {
        section.append(element("p", "kit-muted", "No hashtags yet."));
    }

    const field = element("form", "kit-hashtag-search");
    const label = element("label", null, "YouTube search topic");
    label.htmlFor = "kit-hashtag-topic";
    const input = element("input");
    input.id = label.htmlFor;
    input.type = "search";
    input.maxLength = 120;
    input.minLength = 2;
    input.required = true;
    input.value = activeKit.topic;
    input.placeholder = "e.g. GTA V police car";
    input.setAttribute("aria-describedby", "kit-hashtag-help");
    input.addEventListener("input", () => { activeKit.topic = input.value; });
    const button = element("button", "button button-outline", "Find hashtags");
    button.id = "kit-search-hashtags";
    button.type = "submit";
    button.disabled = Boolean(activeKit.pending);
    button.prepend(icon("search"));
    const controls = element("div", "kit-hashtag-controls");
    controls.append(input, button);
    const help = element("p", "kit-muted", "Only this topic is used to search public YouTube videos.");
    help.id = "kit-hashtag-help";
    field.append(label, controls, help);
    field.addEventListener("submit", findKitHashtags);
    section.append(field);
    return section;
}

function postText(clip) {
    return [clip.description, kitTags(clip).join(" ")].filter(Boolean).join("\n\n");
}

function titlePicker(clip, selectedTitle) {
    const group = element("fieldset", "kit-titles");
    group.append(element("legend", null, "Choose your title"));
    for (const [index, title] of [clip.title, ...(clip.title_variants || [])].entries()) {
        if (!title) continue;
        const option = element("label", "kit-title-choice");
        const radio = element("input");
        radio.type = "radio";
        radio.name = "kit-selected-title";
        radio.value = title;
        radio.checked = selectedTitle ? title === selectedTitle : index === 0;
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

function renderKit(clip, selectedTitle) {
    const content = byId("kit-draft");
    byId("kit-context").textContent = clip.copy_model ? "Drafted by " + clip.copy_model : "Publish your clip";
    const available = clip.copy_source === "ai";
    byId("kit-copy-post").hidden = !available || !clip.description;
    if (!available) {
        const empty = kitSection("No draft yet", "kit-empty");
        empty.append(element("p", null, "Add a note if the scene carries the story, then generate a draft with your local model."));
        content.replaceChildren(empty, hashtagSection(clip));
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
    main.append(titlePicker(clip, selectedTitle));
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

    support.append(hashtagSection(clip));

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

function openPostingKit(clip, folder, sourceTitle, trigger) {
    const topic = clip.hashtag_research?.topic || (clip.copy_source === "ai" ? clip.title : sourceTitle);
    activeKit = {clip, folder, trigger, topic: (topic || "").slice(0, 120)};
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

function setKitPending(request, pending) {
    request.pending = pending;
    byId("kit-refresh").disabled = pending;
    byId("kit-search-hashtags").disabled = pending;
}

async function findKitHashtags(event) {
    event.preventDefault();
    const request = activeKit;
    if (request.pending) return;
    setKitPending(request, true);
    kitStatus("Searching YouTube for relevant hashtags…");
    try {
        const research = await api(kitPath(request) + "/hashtags", jsonRequest("POST", {topic: request.topic.trim()}));
        request.clip.hashtag_research = research;
        if (activeKit === request && kitDialog.open) {
            renderKit(request.clip, chosenTitle());
            kitStatus("YouTube hashtags added to the post.");
        }
    } catch (error) {
        if (activeKit === request && kitDialog.open) {
            const detail = error.message === "Not Found"
                ? "This studio server is missing hashtag search. Close the studio window, run start_web.bat, then reload this page."
                : error.message;
            kitStatus(detail, true);
        }
    } finally {
        if (activeKit === request) {
            setKitPending(request, false);
            if (kitDialog.open && !kitDialog.contains(document.activeElement)) {
                byId("kit-search-hashtags").focus({preventScroll: true});
            }
        }
    }
}

byId("kit-refresh").addEventListener("click", async () => {
    const request = activeKit;
    const button = byId("kit-refresh");
    if (request.pending) return;
    setKitPending(request, true);
    kitStatus("Generating with the local model…");
    try {
        const refreshed = await api(kitPath(request), jsonRequest("POST", {notes: byId("kit-notes-input").value.trim()}));
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
        if (activeKit === request) setKitPending(request, false);
    }
});

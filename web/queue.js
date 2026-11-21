"use strict";

const phases = [
    ["source", "Source"],
    ["transcript", "Transcript"],
    ["selection", "Selection"],
    ["export", "Copy & render"],
    ["finalize", "Save clips"],
];
const statusLabels = {queued: "Waiting", running: "Running", completed: "Ready", failed: "Stopped"};
const knownJobs = new Map(JSON.parse(sessionStorage.getItem("studio-queue-status") || "[]"));
let queueSnapshot = [];
let queueRequest;
let queueLoaded = false;
let latestFinishedAt = Date.now() / 1000;

function taskStages(job) {
    const steps = element("ol", "queue-steps");
    steps.setAttribute("aria-label", "Task stages");
    const current = phases.findIndex(([key]) => key === job.phase);
    for (const [index, [, title]] of phases.entries()) {
        const reached = index === current;
        const state = index < current ? "Done" : reached ? "In progress" : "Remaining";
        const step = element("li", index < current ? "complete" : "");
        if (reached) step.setAttribute("aria-current", "step");
        step.append(element("span", null, title), element("span", "step-state", state));
        steps.append(step);
    }
    return steps;
}

function queueCard(job, historyOpen) {
    const card = element("article", "queue-card " + job.status);
    card.id = "queue-job-" + job.job_id;
    card.tabIndex = -1;
    const heading = element("div", "queue-card-heading");
    const name = element("div");
    const title = element("h4", null, job.source);
    title.id = card.id + "-title";
    card.setAttribute("aria-labelledby", title.id);
    name.append(title, element("code", null, "Task #" + job.job_id));
    const label = job.status === "queued" && job.position ? "Waiting · #" + job.position : statusLabels[job.status];
    heading.append(name, element("span", "queue-status", label));
    card.append(heading);

    const current = element("div", "queue-current");
    if (job.status === "running") {
        const measured = Number.isFinite(job.progress);
        const line = element("div", "queue-current-heading");
        line.append(element("strong", null, job.stage));
        if (measured) line.append(element("span", "queue-percent", job.progress + "% of step"));
        const meter = element("progress", "queue-progress");
        meter.max = 100;
        if (measured) meter.value = job.progress;
        meter.setAttribute("aria-label", job.stage + " progress");
        current.append(line, meter, element("p", null, job.detail), taskStages(job));
    } else if (job.status !== "queued") {
        if (job.status === "failed") current.append(element("strong", null, "Stopped during " + job.stage.toLowerCase()));
        current.append(element("p", null, job.detail));
    }
    if (current.childElementCount) card.append(current);

    if (job.activity.length) {
        const activity = element("details", "queue-activity");
        activity.open = historyOpen;
        const events = element("ol");
        for (const event of job.activity) {
            const row = element("li");
            row.append(element("strong", null, event.stage), document.createTextNode(" · " + event.detail));
            events.append(row);
        }
        const summary = element("summary", null, "Stage history");
        summary.dataset.queueControl = "history";
        activity.append(summary, events);
        card.append(activity);
    }
    if (job.status === "completed" || job.status === "failed") {
        const actions = element("div", "queue-view");
        if (job.status === "completed" || libraryBatchIds.has(job.job_id)) {
            const view = link("View clips", batchHref(job.job_id), "arrow");
            view.dataset.queueControl = "view";
            view.addEventListener("click", () => {
                tasksDrawer.close();
                if (route === "library" && location.hash === view.hash) byId(view.hash.slice(1))?.scrollIntoView();
            });
            actions.append(view);
        }
        const dismiss = action("Dismiss from queue", async () => {
            dismiss.disabled = true;
            try {
                await api("/api/job/" + encodeURIComponent(job.job_id), {method: "DELETE"});
                await refreshQueueAfterRemoval(job.job_id);
            } catch (error) {
                message("queue-error", error.message);
                dismiss.disabled = false;
            }
        });
        dismiss.dataset.queueControl = "dismiss";
        actions.append(dismiss);
        card.append(actions);
    }
    return card;
}

function replaceJobCards(id, jobs) {
    const container = byId(id);
    const focused = document.activeElement.closest(".queue-card");
    const control = document.activeElement.dataset.queueControl;
    const jobIds = new Set(jobs.map((job) => "queue-job-" + job.job_id));
    for (const card of [...container.children]) {
        if (!jobIds.has(card.id)) {
            if (focused === card) tasksDrawer.querySelector("[autofocus]").focus({preventScroll: true});
            card.remove();
        }
    }
    jobs.forEach((job, index) => {
        let card = byId("queue-job-" + job.job_id);
        const signature = JSON.stringify(job) + libraryBatchIds.has(job.job_id);
        if (card?.dataset.signature !== signature) {
            const updated = queueCard(job, card?.querySelector("details")?.open);
            updated.dataset.signature = signature;
            card?.replaceWith(updated);
            if (card && card === focused) {
                const target = control && updated.querySelector('[data-queue-control="' + control + '"]');
                (target || updated).focus({preventScroll: true});
            }
            card = updated;
        }
        if (card !== container.children[index]) container.insertBefore(card, container.children[index] || null);
    });
}

function renderLatestQueue() {
    const recent = queueSnapshot.filter((job) => job.status === "completed" || job.status === "failed");
    byId("queue-recent-section").hidden = !recent.length;
    replaceJobCards("queue-recent", recent);
}

function renderQueue() {
    const active = queueSnapshot.filter((job) => job.status === "queued" || job.status === "running");
    const waiting = active.filter((job) => job.status === "queued").length;
    const summary = active.length ? (active.length - waiting) + " running · " + waiting + " waiting" : "No active tasks";
    const count = byId("tasks-count");
    count.textContent = active.length > 99 ? "99+" : active.length;
    count.hidden = !active.length;
    tasksButton.setAttribute("aria-label", active.length ? "Tasks, " + active.length + " active" : "Tasks");
    byId("queue-summary").textContent = summary;
    byId("queue-empty").hidden = !!queueSnapshot.length;
    byId("queue-active-section").hidden = !active.length;
    byId("library-empty-title").textContent = active.length ? "Waiting for exports" : "No clips yet";
    byId("library-empty-copy").textContent = active.length
        ? "Finished clips will appear here as jobs complete."
        : "Add a video and queue your first batch. Exports and AI posting kits will appear here.";
    byId("start-link").hidden = active.length > 0;
    replaceJobCards("queue-active", active);
    renderLatestQueue();
}

function trackSubmittedJob(id) {
    knownJobs.set(id, "queued");
    sessionStorage.setItem("studio-queue-status", JSON.stringify([...knownJobs]));
    refreshQueue();
}

function refreshQueue() {
    if (!queueRequest) {
        queueRequest = loadQueue().finally(() => { queueRequest = undefined; });
    }
    return queueRequest;
}

async function refreshQueueAfterRemoval(jobId) {
    await queueRequest;
    clearTaskFlash(jobId);
    await refreshQueue();
}

async function loadQueue() {
    let jobs;
    try {
        jobs = await api("/api/jobs", {cache: "no-store"});
    } catch {
        byId("queue-summary").textContent = queueLoaded ? "Showing last update" : "Tasks unavailable";
        byId("queue-empty").hidden = true;
        message("queue-error", "Task updates are unavailable. Retrying automatically.");
        return;
    }

    const results = jobs.filter((job) => job.status === "completed" || job.status === "failed");
    const finished = results.filter((job) => {
        const previous = knownJobs.get(job.job_id);
        return previous === "queued" || previous === "running"
            || (!previous && job.finished_at > latestFinishedAt);
    });
    for (const job of results) latestFinishedAt = Math.max(latestFinishedAt, job.finished_at || 0);
    knownJobs.clear();
    for (const job of jobs) knownJobs.set(job.job_id, job.status);
    sessionStorage.setItem("studio-queue-status", JSON.stringify([...knownJobs]));

    queueSnapshot = jobs;
    renderQueue();
    message("queue-error");
    if (finished.length) {
        showQueueFlash(finished);
        loadStorage();
        if (queueLoaded && route !== "settings") loadTasks();
    }
    queueLoaded = true;
}

refreshQueue().then(() => {
    if (route !== "settings") loadTasks();
});
setInterval(refreshQueue, 1600);

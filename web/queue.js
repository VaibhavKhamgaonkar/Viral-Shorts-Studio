"use strict";

const phases = [
    ["source", "Source"],
    ["transcript", "Transcript"],
    ["selection", "Selection"],
    ["export", "Copy & render"],
    ["finalize", "Save clips"],
];
const statusLabels = {queued: "Queued", running: "In progress", completed: "Ready", failed: "Failed"};
const knownJobs = new Map(JSON.parse(sessionStorage.getItem("studio-queue-status") || "[]"));
let queueSnapshot = [];
let queueRequestInFlight = false;
let lastSnapshot = "";
let queueLoaded = false;

function queueCard(job, openActivities) {
    const card = element("article", "queue-card " + job.status);
    const heading = element("div", "queue-card-heading");
    const name = element("div");
    name.append(element("h4", null, job.source), element("code", null, "Task #" + job.job_id));
    const label = job.status === "queued" && job.position ? "Queued · #" + job.position : statusLabels[job.status];
    heading.append(name, element("span", "queue-status", label));
    card.append(heading);

    if (job.status === "running") {
        const steps = element("ol", "queue-steps");
        steps.setAttribute("aria-label", "Job stages");
        const phaseIndex = phases.findIndex(([key]) => key === job.phase);
        for (const [index, [key, title]] of phases.entries()) {
            const step = element("li", index < phaseIndex ? "complete" : "", title);
            if (key === job.phase) step.setAttribute("aria-current", "step");
            if (index < phaseIndex) step.prepend(element("span", "sr-only", "Done: "));
            steps.append(step);
        }
        card.append(steps);
    }

    const current = element("div", "queue-current");
    if (job.status === "running") {
        current.append(element("strong", null, job.stage));
        if (Number.isFinite(job.progress)) {
            const progress = element("progress", "queue-progress");
            progress.max = 100;
            progress.value = job.progress;
            progress.setAttribute("aria-label", job.stage + " progress");
            current.append(progress, element("p", "queue-percent", job.progress + "% · " + job.detail));
        }
    }
    if (job.status !== "queued" && !Number.isFinite(job.progress)) {
        current.append(element("p", null, job.detail));
    }
    card.append(current);

    if (job.activity.length) {
        const activity = element("details", "queue-activity");
        activity.dataset.jobActivity = job.job_id;
        activity.open = openActivities.has(job.job_id);
        const events = element("ol");
        for (const event of job.activity) {
            const row = element("li");
            row.append(element("strong", null, event.stage), document.createTextNode(" · " + event.detail));
            events.append(row);
        }
        activity.append(element("summary", null, "Stage history"), events);
        card.append(activity);
    }
    if (job.status === "completed" && libraryBatchIds.has(job.job_id)) {
        const actions = element("div", "queue-view");
        actions.append(link("View clips", "#batch-" + job.job_id, "arrow"));
        card.append(actions);
    }
    return card;
}

function replaceJobCards(id, jobs) {
    const container = byId(id);
    const focused = document.activeElement.closest("details[data-job-activity]")?.dataset.jobActivity;
    const open = new Set(Array.from(container.querySelectorAll("details[open]"), (item) => item.dataset.jobActivity));
    container.replaceChildren(...jobs.map((job) => queueCard(job, open)));
    if (focused) container.querySelector('[data-job-activity="' + CSS.escape(focused) + '"] summary')?.focus({preventScroll: true});
}

function renderLatestQueue() {
    const recent = queueSnapshot.filter((job) => job.status === "completed" || job.status === "failed");
    replaceJobCards("queue-latest", recent.slice(0, 1));
    replaceJobCards("queue-past", recent.slice(1));
    byId("queue-history").hidden = recent.length <= 1;
}

function renderQueue() {
    const active = queueSnapshot.filter((job) => job.status === "queued" || job.status === "running");
    const waiting = active.filter((job) => job.status === "queued").length;
    byId("queue-summary").textContent = active.length
        ? (active.length - waiting) + " running · " + waiting + " waiting"
        : "No jobs waiting";
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

async function refreshQueue() {
    if (queueRequestInFlight) return;
    queueRequestInFlight = true;
    try {
        const jobs = await api("/api/jobs");
        const finished = jobs.filter((job) => {
            const previous = knownJobs.get(job.job_id);
            return (previous === "queued" || previous === "running")
                && (job.status === "completed" || job.status === "failed");
        });
        const newResults = jobs.some((job) => ["completed", "failed"].includes(job.status)
            && knownJobs.get(job.job_id) !== job.status);
        knownJobs.clear();
        for (const job of jobs) knownJobs.set(job.job_id, job.status);
        sessionStorage.setItem("studio-queue-status", JSON.stringify([...knownJobs]));
        const signature = JSON.stringify(jobs);
        if (signature !== lastSnapshot) {
            lastSnapshot = signature;
            queueSnapshot = jobs;
            renderQueue();
        }
        if (finished.length) {
            const notice = finished.length === 1
                ? "Task #" + finished[0].job_id + " · " + finished[0].detail
                : finished.length + " tasks finished. See the task queue for results.";
            byId("queue-announcement").textContent = notice;
            toast(notice);
            loadStorage();
        }
        if (queueLoaded && newResults && route !== "settings") loadTasks();
        queueLoaded = true;
        message("queue-error");
    } catch (error) {
        message("queue-error", "Queue status unavailable. Retrying automatically.");
    } finally {
        queueRequestInFlight = false;
    }
}

refreshQueue().then(() => {
    if (route !== "settings") loadTasks();
});
setInterval(refreshQueue, 1600);

"use strict";

const phases = [
    ["source", "Source"],
    ["transcript", "Transcript"],
    ["selection", "Selection"],
    ["export", "Copy & render"],
    ["finalize", "Save clips"],
];
let flashTimer;
const tracker = byId("task-tracker");
const trackerToggle = tracker.querySelector("summary");
const queueAnchor = route === "settings" ? "/library#task-queue" : "#task-queue";

function phaseIndex(job) {
    return phases.findIndex(([key]) => key === job.phase);
}

function progressMeter(job, className) {
    const meter = element("progress", className);
    meter.max = 100;
    if (Number.isFinite(job.progress)) meter.value = job.progress;
    meter.setAttribute("aria-label", job.stage + " progress");
    return meter;
}

function renderHeaderTracker(job, waiting) {
    const running = job?.status === "running";
    const complete = job?.status === "completed";
    const stopped = job?.status === "failed";
    const index = job ? phaseIndex(job) : -1;
    const measured = running && Number.isFinite(job.progress);
    const stage = running ? job.stage : complete ? "Clips ready" : stopped ? "Task stopped" : "Task progress";
    const state = running
        ? "Stage " + (index + 1) + " of " + phases.length
        : job ? statusLabels[job.status] : "No tasks running";
    const summary = job ? ["#" + job.job_id, state] : [state];
    if (measured) summary.push(job.progress + "% of step");
    if (waiting) summary.push(waiting + " waiting");
    byId("tracker-stage").textContent = stage;
    byId("tracker-meta").textContent = summary.join(" · ");

    const bar = byId("tracker-bar");
    bar.replaceChildren(...phases.map((_, step) => {
        const done = complete || step < index;
        const segment = element("span", "tracker-segment");
        if (done) segment.classList.add("done");
        if (step === index && stopped) segment.classList.add("stopped");
        if (step === index && running) segment.append(progressMeter(job, "tracker-meter"));
        return segment;
    }));

    const detail = byId("tracker-detail");
    if (!job) {
        detail.replaceChildren(element("p", null, "Queue a video to see its stages and progress here."));
        return;
    }
    const parts = [
        element("p", "tracker-task-name", job.source),
        element("p", null, "Task #" + job.job_id + " · " + (stopped && index >= 0 ? "Stopped during " + job.stage : job.stage)),
        element("p", null, job.detail),
    ];
    if (index >= 0 || complete) {
        const steps = element("ol", "tracker-steps");
        steps.setAttribute("aria-label", "Task stages");
        phases.forEach(([, label], step) => {
            const current = step === index && !complete;
            const item = element("li");
            if (current) item.setAttribute("aria-current", "step");
            const status = complete || step < index ? "Done"
                : current ? (stopped ? "Stopped here" : measured ? job.progress + "% of step" : "In progress")
                    : "Remaining";
            item.append(element("span", null, label), element("span", null, status));
            steps.append(item);
        });
        parts.push(steps);
    }
    detail.replaceChildren(...parts);
}

function showQueueFlash(jobs) {
    const flash = byId("queue-flash");
    const result = jobs.length === 1 ? jobs[0] : null;
    const failures = jobs.filter((job) => job.status === "failed").length;
    const title = result
        ? (failures ? "Task stopped" : "Clips ready") + " · #" + result.job_id
        : jobs.length + " tasks finished";
    const detail = result
        ? (failures ? "Open the queue for details." : result.detail)
        : (jobs.length - failures) + " completed · " + failures + " stopped";
    const link = byId("queue-flash-link");
    link.href = result && !failures ? "/library#batch-" + result.job_id : queueAnchor;
    link.textContent = result && !failures ? "View clips" : "Open queue";
    byId("queue-flash-title").textContent = title;
    byId("queue-flash-detail").textContent = detail;
    flash.dataset.state = failures ? "failed" : "complete";
    flash.hidden = false;
    byId("queue-announcement").textContent = title + ". " + detail;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => {
        if (!flash.contains(document.activeElement)) flash.hidden = true;
    }, 7500);
}

byId("tracker-queue-link").href = queueAnchor;
byId("tracker-queue-link").addEventListener("click", () => { tracker.open = false; });
byId("dismiss-queue-flash").addEventListener("click", () => {
    clearTimeout(flashTimer);
    byId("queue-flash").hidden = true;
    trackerToggle.focus();
});
document.addEventListener("click", (event) => {
    if (tracker.open && !tracker.contains(event.target)) tracker.open = false;
});
tracker.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && tracker.open) {
        event.preventDefault();
        tracker.open = false;
        trackerToggle.focus();
    }
});

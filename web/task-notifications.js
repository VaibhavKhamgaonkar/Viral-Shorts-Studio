"use strict";

const tasksDrawer = byId("tasks-drawer");
const tasksButton = byId("tasks-button");
const taskNotice = byId("queue-flash");
let flashTimer;
let flashJobs = [];

function openTasks(jobId) {
    if (!tasksDrawer.open) tasksDrawer.showModal();
    if (jobId) byId("queue-job-" + jobId)?.focus();
}

function batchHref(jobId) {
    return "/library#batch-" + encodeURIComponent(jobId);
}

function hideQueueFlash() {
    clearTimeout(flashTimer);
    taskNotice.hidden = true;
    flashJobs = [];
    byId("queue-announcement").textContent = "";
}

function scheduleFlashDismissal() {
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => {
        if (!taskNotice.matches(":hover, :focus-within")) hideQueueFlash();
    }, 7500);
}

function showQueueFlash(jobs) {
    flashJobs = jobs;
    const result = jobs.length === 1 ? jobs[0] : null;
    const failures = jobs.filter((job) => job.status === "failed").length;
    const ready = result?.status === "completed";
    const title = result
        ? (ready ? "Clips ready" : "Task stopped") + " · #" + result.job_id
        : jobs.length + " tasks finished";
    const detail = result
        ? (ready ? result.detail : result.source + " stopped during " + result.stage.toLowerCase() + ".")
        : (jobs.length - failures) + " completed · " + failures + " stopped";

    const link = byId("queue-flash-link");
    link.hidden = !ready;
    if (ready) link.href = batchHref(result.job_id);
    byId("queue-flash-details").hidden = ready;
    byId("queue-flash-title").textContent = title;
    byId("queue-flash-detail").textContent = detail;
    taskNotice.dataset.state = failures ? "failed" : "complete";
    taskNotice.hidden = false;
    byId("queue-announcement").textContent = title + ". " + detail;
    scheduleFlashDismissal();
}

function clearTaskFlash(jobId) {
    const remaining = flashJobs.filter((job) => job.job_id !== jobId);
    if (remaining.length === flashJobs.length) return;
    if (remaining.length) showQueueFlash(remaining);
    else hideQueueFlash();
}

tasksButton.addEventListener("click", () => openTasks());
byId("queue-flash-details").addEventListener("click", () => {
    const jobId = flashJobs.length === 1 ? flashJobs[0].job_id : "";
    tasksButton.focus({preventScroll: true});
    hideQueueFlash();
    openTasks(jobId);
});
byId("dismiss-queue-flash").addEventListener("click", () => {
    hideQueueFlash();
    tasksButton.focus({preventScroll: true});
});
taskNotice.addEventListener("pointerleave", scheduleFlashDismissal);
taskNotice.addEventListener("focusout", (event) => {
    if (!taskNotice.contains(event.relatedTarget)) scheduleFlashDismissal();
});
tasksDrawer.addEventListener("click", (event) => {
    if (event.target === tasksDrawer && event.clientX < tasksDrawer.getBoundingClientRect().left) tasksDrawer.close();
});

(() => {
    const jobEl = document.getElementById("job");
    if (!jobEl) {
        return;
    }
    const jobId = jobEl.dataset.jobId;
    const logEl = document.getElementById("log-output");
    const statusEl = document.getElementById("job-status");
    const artifactList = document.getElementById("artifact-list");

    const es = new EventSource(`/api/jobs/${jobId}/log/stream`);
    es.onmessage = (event) => {
        logEl.textContent += event.data + "\n";
        logEl.scrollTop = logEl.scrollHeight;
    };
    es.onerror = () => {
        // Retry will happen automatically; keep minimal handling.
    };

    async function refreshStatus() {
        try {
            const res = await fetch(`/jobs/${jobId}/refresh`);
            if (!res.ok) return;
            const data = await res.json();
            statusEl.textContent = data.status;
        } catch (e) {
            // ignore transient errors
        }
    }

    async function refreshArtifacts() {
        try {
            const res = await fetch(`/api/jobs/${jobId}/artifacts`);
            if (!res.ok) return;
            const items = await res.json();
            artifactList.innerHTML = "";
            if (!items.length) {
                const li = document.createElement("li");
                li.textContent = "No artifacts yet.";
                artifactList.appendChild(li);
                return;
            }
            items.forEach((item) => {
                const li = document.createElement("li");
                const link = document.createElement("a");
                link.href = `/api/jobs/${jobId}/artifacts/${encodeURIComponent(item.name)}`;
                link.textContent = item.name;
                li.appendChild(link);
                li.appendChild(document.createTextNode(` (${item.size} bytes)`));
                artifactList.appendChild(li);
            });
        } catch (e) {
            // ignore transient errors
        }
    }

    setInterval(refreshStatus, 4000);
    setInterval(refreshArtifacts, 5000);

    window.addEventListener("beforeunload", () => es.close());
})();

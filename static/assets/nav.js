// Shared navigation header. Include with:
//   <script src="/static/assets/nav.js" data-active="signals"></script>
(function () {
    const script = document.currentScript;
    const active = script ? script.getAttribute("data-active") : "";
    const pages = [
        ["signals", "/static/signals.html", "Signals"],
        ["calendar", "/static/calendar.html", "Calendar"],
        ["paper", "/static/paper.html", "Paper Book"],
        ["rankings", "/static/index.html", "Rankings"],
        ["status", "/docs", "API"],
    ];
    function render() {
        const nav = document.createElement("nav");
        nav.className = "nav";
        for (const [key, href, label] of pages) {
            const a = document.createElement("a");
            a.href = href;
            a.textContent = label;
            if (key === active) a.className = "active";
            nav.appendChild(a);
        }
        const container = document.querySelector(".container") || document.body;
        container.insertBefore(nav, container.firstChild);
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", render);
    } else {
        render();
    }
})();

/* Site Desk front-end. Reads config.json (locations) and jobs.json (daily data). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const state = {
    cfg: null, data: null, jobs: [], regionInfo: {}, regionOrder: [],
    q: "", group: "All", regions: new Set(), type: "All", sort: "score",
    minScore: 0, onlyNew: false, onlyReputed: false, onlySalary: false,
  };

  /* Safe DOM builder: text is always inserted as text, never as HTML. */
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("data-") || k.startsWith("aria-") || ["href", "target", "rel", "type", "role", "style"].includes(k)) el.setAttribute(k, v);
      else el[k] = v;
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");

  function relDate(iso) {
    const d = new Date(iso + "T00:00:00");
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = Math.round((today - d) / 86400000);
    if (diff <= 0) return "Today";
    if (diff === 1) return "Yesterday";
    return diff + " days ago";
  }

  /* ---------- load ---------- */
  async function load() {
    const bust = "?t=" + Date.now();
    try {
      const [cfg, data] = await Promise.all([
        fetch("config.json" + bust).then((r) => r.json()),
        fetch("jobs.json" + bust).then((r) => r.json()),
      ]);
      state.cfg = cfg; state.data = data; state.jobs = data.jobs || [];
    } catch (err) {
      $("#lastUpdated").textContent = "could not load data (open via a web server, not file://)";
      return;
    }
    const locs = state.cfg.locations.filter((l) => l.enabled !== false);
    locs.forEach((l) => { state.regionInfo[l.name] = { group: l.group, priority: l.priority }; });
    state.regionInfo["Remote"] = { group: "Remote", priority: 50 };
    state.regionInfo["Other India"] = { group: "India", priority: 98 };
    state.regionInfo["Other Abroad"] = { group: "Abroad", priority: 99 };
    state.regionOrder = Object.keys(state.regionInfo).sort((a, b) => state.regionInfo[a].priority - state.regionInfo[b].priority);

    $("#lastUpdated").textContent = state.data.last_updated_display || "unknown";
    $("#demoBanner").hidden = !state.data.demo;
    bind(); render(); renderLive();
  }

  /* ---------- live-search links (LinkedIn / Naukri / Indeed / Google Jobs) ---------- */
  const LIVE_ROLES = ["Planning Engineer", "Planning Manager", "Billing Engineer", "Billing Manager", "Quantity Surveyor"];
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const placeOf = (l) => (l.adzuna && l.adzuna.where) || (l.jsearch ? l.jsearch.split(",")[0] : l.name);

  function liveLinks(role, place, group) {
    const q = encodeURIComponent(role), p = encodeURIComponent(place);
    const links = [
      ["LinkedIn", "https://www.linkedin.com/jobs/search/?keywords=" + q + "&location=" + p + "&f_TPR=r604800&sortBy=DD"],
      ["Google Jobs", "https://www.google.com/search?q=" + encodeURIComponent(role + " jobs in " + place) + "&ibp=htl;jobs"],
    ];
    if (group === "India") {
      links.splice(1, 0, ["Naukri", "https://www.naukri.com/" + slug(role) + "-jobs-in-" + slug(place)]);
      links.splice(2, 0, ["Indeed", "https://in.indeed.com/jobs?q=" + q + "&l=" + p + "&fromage=7&sort=date"]);
    }
    return links;
  }

  function renderLive() {
    const locs = state.cfg.locations.filter((l) => l.enabled !== false);
    const sel = $("#liveLoc");
    sel.replaceChildren(...locs.map((l, i) => h("option", { value: String(i) }, l.name)));
    const draw = () => {
      const l = locs[Number(sel.value)] || locs[0];
      const place = placeOf(l);
      $("#liveLinks").replaceChildren(...LIVE_ROLES.map((role) =>
        h("div", { class: "live-row" },
          h("span", { class: "live-role" }, role + " · " + place),
          h("span", { class: "live-btns" }, liveLinks(role, place, l.group).map(([name, url]) =>
            h("a", { class: "live-btn", href: url, target: "_blank", rel: "noopener noreferrer" }, name))))));
    };
    sel.addEventListener("change", draw);
    draw();
  }

  /* ---------- filtering ---------- */
  function matches(j, skipRegion) {
    const info = state.regionInfo[j.region] || { group: j.group };
    if (state.group !== "All" && info.group !== state.group) return false;
    if (!skipRegion && state.regions.size && !state.regions.has(j.region)) return false;
    if (state.type !== "All" && j.type !== state.type) return false;
    if (j.score < state.minScore) return false;
    if (state.onlyNew && j.first_seen !== state.data.run_date) return false;
    if (state.onlyReputed && !j.reputed) return false;
    if (state.onlySalary && !j.salary_text) return false;
    if (state.q) {
      const hay = [j.title, j.company, j.location, j.snippet, (j.matched_skills || []).join(" "), (j.gaps || []).join(" ")].join(" ").toLowerCase();
      if (!state.q.split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  function sorted(list) {
    const pri = (j) => (state.regionInfo[j.region] || { priority: 99 }).priority;
    const by = {
      score: (a, b) => b.score - a.score || b.posted.localeCompare(a.posted),
      newest: (a, b) => b.posted.localeCompare(a.posted) || b.score - a.score,
      location: (a, b) => pri(a) - pri(b) || b.score - a.score,
    }[state.sort];
    return list.slice().sort(by);
  }

  /* ---------- render ---------- */
  function renderStats() {
    const jobs = state.jobs, run = state.data.run_date;
    const stat = (n, label) => h("div", { class: "stat" }, h("b", {}, n), h("span", {}, label));
    $("#stats").replaceChildren(
      stat(jobs.length, "Open matches"),
      stat(jobs.filter((j) => j.first_seen === run).length, "New today"),
      stat(jobs.filter((j) => j.reputed).length, "Reputed employers"),
      stat(new Set(jobs.map((j) => j.region)).size, "Locations covered"),
    );
  }

  function renderGroups() {
    const groups = ["All", "India", "Abroad", "Remote"];
    $("#groups").replaceChildren(...groups.map((g) =>
      h("button", { type: "button", role: "tab", "aria-selected": String(state.group === g), onclick: () => { state.group = g; state.regions.clear(); render(); } }, g)));
  }

  function renderChips() {
    const present = new Set(state.jobs.map((j) => j.region));
    const chips = state.regionOrder.filter((r) => {
      const g = state.regionInfo[r].group;
      if (state.group !== "All" && g !== state.group) return false;
      return !r.startsWith("Other") && r !== "Remote" ? true : present.has(r);
    }).map((r) => {
      const n = state.jobs.filter((j) => j.region === r && matches(j, true)).length;
      return h("button", {
        type: "button", class: "chip", "aria-pressed": String(state.regions.has(r)), disabled: n === 0 && !state.regions.has(r),
        onclick: () => { state.regions.has(r) ? state.regions.delete(r) : state.regions.add(r); render(); },
      }, r, h("small", {}, n));
    });
    $("#chips").replaceChildren(...chips);
  }

  function card(j) {
    const tier = j.score >= 75 ? "high" : j.score >= 60 ? "mid" : "";
    const isNew = j.first_seen === state.data.run_date;
    const salary = j.salary_text
      ? h("div", { class: "salary" }, "💰 ", j.salary_text)
      : h("div", { class: "salary" }, "💰 Not disclosed", j.market_band ? h("span", { class: "band" }, " · typical " + j.market_band) : null);
    return h("article", { class: "card " + tier },
      h("div", { class: "card-top" },
        h("div", {},
          h("h3", { class: "title" }, j.title),
          h("p", { class: "company" }, j.company)),
        h("div", { class: "ring", style: "--p:" + Math.round(j.score), "aria-label": "Match " + Math.round(j.score) + " percent" }, h("span", {}, Math.round(j.score)))),
      h("div", { class: "badges" },
        isNew && h("span", { class: "badge new" }, "NEW"),
        j.reputed && h("span", { class: "badge rep" }, "✔ Reputed"),
        j.type === "Consultancy" && h("span", { class: "badge cons" }, "Consultancy"),
        j.remote && h("span", { class: "badge" }, "Remote"),
        h("span", { class: "badge" }, j.level)),
      h("div", { class: "meta" },
        h("span", {}, "📍 " + j.location),
        h("span", {}, "🗓 " + relDate(j.posted)),
        j.experience && h("span", {}, "🎯 " + j.experience)),
      salary,
      (j.matched_skills || []).length
        ? h("div", { class: "skills", "aria-label": "Skills matched from your CV" }, j.matched_skills.slice(0, 8).map((s) => h("span", { class: "skill" }, s)))
        : null,
      (j.gaps || []).length ? h("div", { class: "gap" }, "Also asks for: " + j.gaps.join(", ")) : null,
      j.snippet ? h("p", { class: "snippet" }, j.snippet) : null,
      h("a", { class: "apply", href: safeUrl(j.url), target: "_blank", rel: "noopener noreferrer" }, "Apply Now →"));
  }

  function render() {
    renderStats(); renderGroups(); renderChips();
    const list = sorted(state.jobs.filter((j) => matches(j, false)));
    $("#count").textContent = list.length + (list.length === 1 ? " job" : " jobs") + " shown";
    $("#grid").replaceChildren(...list.map(card));
    $("#empty").hidden = list.length > 0;
  }

  /* ---------- controls ---------- */
  function bind() {
    $("#q").addEventListener("input", (e) => { state.q = e.target.value.trim().toLowerCase(); render(); });
    $("#type").addEventListener("change", (e) => { state.type = e.target.value; render(); });
    $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; render(); });
    $("#minScore").addEventListener("input", (e) => {
      state.minScore = Number(e.target.value); $("#minScoreLabel").textContent = state.minScore + "%"; render();
    });
    $("#onlyNew").addEventListener("change", (e) => { state.onlyNew = e.target.checked; render(); });
    $("#onlyReputed").addEventListener("change", (e) => { state.onlyReputed = e.target.checked; render(); });
    $("#onlySalary").addEventListener("change", (e) => { state.onlySalary = e.target.checked; render(); });
    $("#theme").addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("sitedesk-theme", next); } catch (_) { /* storage may be blocked */ }
    });
  }

  try {
    const saved = localStorage.getItem("sitedesk-theme");
    if (saved) document.documentElement.dataset.theme = saved;
    else if (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark";
  } catch (_) { /* ignore */ }

  load();
})();

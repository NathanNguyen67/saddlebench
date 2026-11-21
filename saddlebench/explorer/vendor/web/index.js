/* Structure index: one card per structure, animating a representative reaction on its starting minimum,
   plus a sortable table. Rows come from build_index.py (#index-data); each card holds a one-reaction mini payload. */
window.SM = window.SM || {};

(function index() {
  const { h, fmt, config } = SM;
  const data = JSON.parse(document.getElementById("index-data").textContent);
  const rows = data.rows;
  const methods = data.methods || [];
  const state = { q: "", ds: new Set(), view: "cards", key: config.mode === "bench" ? "reference" : "seedReactions", dir: -1,
    method: methods[0]?.id, cutoff: "0.20", missedOnly: false };
  try { const saved = localStorage.getItem("sm-index-view"); if (saved === "table" || saved === "cards") state.view = saved; } catch (error) { /* storage unavailable */ }
  const q0 = new URLSearchParams(location.hash.slice(1));
  if (q0.get("m") && methods.some((m) => m.id === q0.get("m"))) state.method = q0.get("m");
  if (q0.get("x")) state.cutoff = q0.get("x");

  const renderer = new SM.Renderer();
  const root = document.getElementById("index-root");
  const grid = h("div", { class: "card-grid structure-grid" });
  const tableBox = h("div", { class: "box", hidden: true });
  const shown = h("span", { class: "muted result-count" });
  const cards = new Map();
  const visible = new Set();
  const still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  const scoreOf = (r) => {
    if (!r.score) return null;
    const found = r.score.found[state.method]?.[state.cutoff];
    return found === undefined ? null : { found, reference: r.score.reference, missed: r.score.reference - found };
  };
  const link = (r) => `${r.slug}.html${methods.length ? `#m=${encodeURIComponent(state.method)}&x=${state.cutoff}` : ""}`;

  const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
    const card = cards.get(entry.target.dataset.slug);
    if (entry.isIntersecting) {
      if (!card.viewer && card.row.mini) {
        const store = new SM.Store(card.row.mini);
        store.lattice = new SM.geo.Lattice(store.cell, store.pbc);
        const settings = new SM.ViewSettings({ radius: card.row.radius || 9, yaw: 0.55, pitch: -0.95, fade: 0.35 });
        card.viewer = new SM.Viewer(card.canvas, store, renderer, settings, { interactive: false, overlays: false });
        card.viewer.setReaction(0);
      }
      visible.add(card);
    } else visible.delete(card);
  }), { rootMargin: "160px" });

  let cursor = 0;
  const tick = (time) => {
    const list = [...visible].filter((c) => c.viewer);
    const budget = Math.min(list.length, 4);
    for (let i = 0; i < budget; i += 1) {
      const card = list[(cursor + i) % list.length];
      card.viewer.settings.t = Math.sin(time / 1000 + card.phase);
      card.viewer.draw();
    }
    cursor += budget;
    if (!still) requestAnimationFrame(tick);
  };

  function card(r) {
    if (cards.has(r.slug)) return cards.get(r.slug);
    const canvas = h("canvas", { class: "card-canvas", "aria-hidden": "true" });
    canvas.dataset.slug = r.slug;
    const scoreLine = h("span", { class: "card-facts card-scoreline" });
    const node = h("a", { class: "card structure-card", href: link(r) },
      canvas,
      h("span", { class: "card-head" }, h("b", {}, r.short), h("span", {}, r.formula || "")),
      r.mechanism ? h("span", { class: "card-mech" }, r.mechanism) : r.name !== r.short ? h("span", { class: "card-mech muted" }, r.name) : null,
      h("span", { class: "card-facts" }, [fmt.count(r.atoms, "atom"), fmt.count(r.classes, "class"),
        config.mode === "bench" ? fmt.count(r.score?.reference ?? r.reference ?? 0, "reference saddle") : fmt.count(r.seedReactions, "seed reaction")].join(" · ")),
      scoreLine);
    const entry = { node, canvas, row: r, viewer: null, phase: r.atoms * 0.13, scoreLine };
    cards.set(r.slug, entry);
    observer.observe(canvas);
    return entry;
  }

  function updateCard(entry) {
    entry.node.href = link(entry.row);
    const s = scoreOf(entry.row);
    if (!s) { entry.scoreLine.replaceChildren(); return; }
    const pct = s.reference ? (100 * s.found) / s.reference : 0;
    SM.fill(entry.scoreLine,
      h("span", { class: "meter", title: `${s.found} of ${s.reference} found` }, h("i", { style: `width:${pct.toFixed(1)}%` })),
      h("span", {}, `${s.found}/${s.reference} found`), s.missed ? h("span", { class: "missed-count" }, ` · ${s.missed} missed`) : null);
  }

  const cols = [
    ["name", "Structure", "l", (r) => r.parent],
    ["atoms", "Atoms", "", (r) => r.atoms],
    ["classes", "Classes", "", (r) => r.classes],
    ["saddles", "Saddles", "", (r) => r.saddles, "Distinct saddles with a complete DoubleMin in the explorer"],
    ["seedReactions", "Seed reactions", "", (r) => r.seedReactions, "Distinct endpoint pairs that include the starting minimum, barrier below 1 eV"],
  ];
  if (config.mode === "bench") {
    cols.push(["reference", "Reference saddles", "", (r) => r.score?.reference ?? r.reference ?? 0, "Saddle groups SaddleBench scores for this seed"]);
    cols.push(["missed", "Missed", "", (r) => scoreOf(r)?.missed ?? -1, "Reference saddles the selected method did not find"]);
  }
  cols.push(["lowest", "Lowest barrier (eV)", "", (r) => (r.lowest == null ? 99 : r.lowest)]);
  if (config.mode !== "bench") {
    cols.push(["actual", "Actual tail", "", (r) => (r.actual == null ? -1 : r.actual), "Consecutive qualifying random outcomes since the last new reaction"]);
    cols.push(["shuffled", "Shuffled tail", "", (r) => (r.tail == null ? -1 : r.tail), "Mean tail over 400 shuffles"]);
  }

  function cell(key, r) {
    if (key === "name") return h("td", { class: "l" }, h("a", { href: link(r) }, r.short), h("span", { class: "ds" }, r.mechanism || r.name));
    if (key === "missed") { const s = scoreOf(r); return h("td", { class: s && s.missed ? "missed-count" : "" }, s ? String(s.missed) : "–"); }
    if (key === "lowest") return h("td", {}, r.lowest == null ? "–" : r.lowest.toFixed(3));
    if (key === "actual" || key === "shuffled") { const v = key === "actual" ? r.actual : r.tail; return h("td", { class: v >= 200 ? "met" : "" }, v == null ? "–" : Math.round(v).toLocaleString("en-US")); }
    const value = cols.find((c) => c[0] === key)[3](r);
    return h("td", {}, Number(value).toLocaleString("en-US"));
  }

  function render() {
    const query = state.q.toLowerCase();
    const list = rows.filter((r) => (!state.ds.size || state.ds.has(r.dataset))
      && (!state.missedOnly || (scoreOf(r)?.missed > 0))
      && (!query || `${r.parent} ${r.short} ${r.mechanism || ""} ${r.formula || ""}`.toLowerCase().includes(query)));
    const f = (cols.find((c) => c[0] === state.key) || cols[0])[3];
    list.sort((a, b) => { const x = f(a); const y = f(b); return (x < y ? -1 : x > y ? 1 : 0) * state.dir || a.parent.localeCompare(b.parent); });
    shown.textContent = `${list.length} of ${rows.length} structures`;
    grid.hidden = state.view !== "cards";
    tableBox.hidden = state.view !== "table";
    if (state.view === "cards") {
      grid.replaceChildren(...list.map((r) => { const c = card(r); updateCard(c); return c.node; }));
      if (!list.length) grid.append(h("p", { class: "empty" }, "No structure matches these filters."));
    } else {
      const head = h("tr", {}, cols.map(([key, label, cls, , tip]) => h("th", { class: cls, title: tip || null },
        h("button", { type: "button", "data-dir": key === state.key ? (state.dir < 0 ? "↓" : "↑") : null,
          onclick: () => { if (state.key === key) state.dir = -state.dir; else { state.key = key; state.dir = key === "name" || key === "lowest" ? 1 : -1; } render(); } }, label))));
      tableBox.replaceChildren(h("table", {}, h("thead", {}, head), h("tbody", {}, list.length
        ? list.map((r) => h("tr", {}, cols.map(([key]) => cell(key, r))))
        : h("tr", {}, h("td", { colspan: cols.length, class: "empty" }, "No structure matches these filters.")))));
    }
  }

  const datasets = [...new Set(rows.map((r) => r.dataset))].sort();
  const chips = datasets.length > 1 ? datasets.map((d) => {
    const chip = h("button", { type: "button", class: "chip tag-none", "aria-pressed": "false", onclick: () => {
      if (state.ds.has(d)) state.ds.delete(d); else state.ds.add(d);
      chip.setAttribute("aria-pressed", String(state.ds.has(d))); render();
    } }, d);
    return chip;
  }) : [];
  const viewButtons = ["cards", "table"].map((v) => h("button", { type: "button", "aria-pressed": String(state.view === v), onclick: (e) => {
    state.view = v;
    viewButtons.forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
    try { localStorage.setItem("sm-index-view", v); } catch (error) { /* storage unavailable */ }
    render();
  } }, v === "cards" ? "Cards" : "Table"));
  const scoreControls = methods.length ? [
    h("label", { class: "field-inline" }, "Scored", h("select", { onchange: (e) => { state.method = e.target.value; render(); } },
      methods.map((m) => h("option", { value: m.id, selected: m.id === state.method }, m.label)))),
    h("label", { class: "field-inline" }, "at", h("select", { onchange: (e) => { state.cutoff = e.target.value; render(); } },
      ["0.20", "0.10", "0.05"].map((c) => h("option", { value: c, selected: c === state.cutoff }, `${c} Å`)))),
    h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { state.missedOnly = e.target.checked; render(); } }), "With misses only"),
  ] : [];
  root.append(
    h("div", { class: "filters" },
      h("input", { type: "search", class: "search", placeholder: "Search structure, reaction or formula", "aria-label": "Search structures", oninput: (e) => { state.q = e.target.value.trim(); render(); } }),
      scoreControls,
      h("div", { class: "segment view-toggle", role: "group", "aria-label": "View" }, viewButtons)),
    chips.length ? h("div", { class: "chips" }, chips) : null,
    shown, grid, tableBox,
    h("p", { class: "muted glossary" }, data.glossary || ""));
  render();
  if (!still) requestAnimationFrame(tick);
  else setTimeout(() => cards.forEach((c) => c.viewer?.draw()), 300);
})();

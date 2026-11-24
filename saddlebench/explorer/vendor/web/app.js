/* Start-up and routing. The URL hash stores the open class and reaction, and the scored method and cutoff. */
window.SM = window.SM || {};

(async function start() {
  const status = document.getElementById("loading");
  try {
    const text = document.getElementById("payload").textContent.trim();
    const stream = new Blob([SM.Store.bytes(text)]).stream().pipeThrough(new DecompressionStream("gzip"));
    const payload = JSON.parse(await new Response(stream).text());
    document.getElementById("payload").remove();
    const store = new SM.Store(payload);
    store.lattice = new SM.geo.Lattice(store.cell, store.pbc);
    const renderer = new SM.Renderer();
    const { h, fmt, config } = SM;
    const p = store.provenance;
    const classCount = store.classes.filter((c) => c.id !== "Unclassified").length;
    const saddles = new Set(store.reactions.map((r) => r.saddle)).size;
    const score = config.score ? new SM.Score(store, config.score) : null;
    store.score = score;

    document.querySelector(".brand-name").textContent = config.brand;
    document.getElementById("structure").textContent = config.title || store.structure.parent;
    const summary = [fmt.count(store.n, "atom"), `${store.nFree} free`, fmt.count(classCount, "class"), fmt.count(saddles, "saddle")];
    if (score) summary.push(fmt.count(score.totals().reference, "reference saddle"));
    document.getElementById("summary").textContent = summary.join(" · ");
    document.getElementById("provenance").textContent = `${p.model} · generation ${p.generation ?? "–"} · snapshot ${p.snapshot} · Hessians ${p.hessians}`;
    const lit = SM.literature(config.lit);
    if (lit) document.getElementById("literature").replaceWith(lit);
    if (config.links.index) {
      const back = document.querySelector(".brand-back");
      back.href = config.links.index;
      back.hidden = false;
    }

    if (config.links.debug) {
      document.querySelector(".brand").append(" ", h("a", { href: config.links.debug, title: "Every saddle (with or without a DoubleMin) and every SaddleMotif guess, by primary atom and site" }, "Debug view →"));
    }

    const galleryRoot = document.getElementById("gallery");
    const workspaceRoot = document.getElementById("workspace");
    let workspace = null;
    let gallery = null;
    const hashState = () => new URLSearchParams(location.hash.slice(1));
    const writeHash = (q) => { try { history.replaceState(null, "", `#${q.toString()}`); } catch (error) { /* embedded views may block URL updates */ } };

    if (score) {
      const q = hashState();
      if (q.get("m") && score.methods.some((m) => m.id === q.get("m"))) score.method = q.get("m");
      if (q.get("x") && score.cutoffs.includes(q.get("x"))) score.cutoff = q.get("x");
      const totals = h("span", { class: "score-totals", role: "status" });
      const method = h("select", { "aria-label": "Method scored", onchange: (e) => score.set(e.target.value) },
        score.methods.map((m) => h("option", { value: m.id, selected: m.id === score.method }, m.label)));
      const cutoff = h("select", { "aria-label": "Match cutoff", onchange: (e) => score.set(null, e.target.value) },
        score.cutoffs.map((c) => h("option", { value: c, selected: c === score.cutoff }, `${c} Å`)));
      const update = () => {
        const t = score.totals();
        SM.fill(totals, h("b", {}, `${t.found} of ${t.reference}`), " reference saddles found",
          t.missed ? h("span", { class: "missed-count" }, ` · ${t.missed} missed`) : null,
          t.families !== null && t.families < t.reference
            ? h("span", { class: "muted", title: "Reference saddles related by a symmetry of the seed (mirror, rotation) count once here" }, ` · ${t.familiesFound} of ${t.families} symmetry-distinct mechanisms`) : null);
        const q2 = hashState(); q2.set("m", score.method); q2.set("x", score.cutoff); writeHash(q2);
      };
      score.listeners.add(update);
      update();
      const notes = config.score.notes ? h("span", { class: "muted score-note" }, config.score.notes) : null;
      SM.fill(document.getElementById("scorebar"), h("label", { class: "field-inline" }, "Scored", method), h("label", { class: "field-inline" }, "at", cutoff), totals, notes);
      document.getElementById("scorebar").hidden = false;
    }

    const route = () => {
      const q = hashState();
      const cls = q.get("c");
      if (cls && store.classById.has(cls)) {
        if (!workspace) {
          workspace = new SM.Workspace(workspaceRoot, store, renderer, {
            back: () => { const q2 = hashState(); q2.delete("c"); q2.delete("r"); location.hash = q2.toString(); },
            changed: (c, r) => { const q2 = hashState(); q2.set("c", c); q2.set("r", r); writeHash(q2); },
          });
          SM.app.workspace = workspace;
        }
        galleryRoot.hidden = true;
        gallery.pause();
        workspaceRoot.hidden = false;
        if (q.get("bonds") === "1") { workspace.settings.bonds = true; if (workspace.bondBox) workspace.bondBox.checked = true; }
        if (q.get("size") && Number(q.get("size")) > 0) workspace.setAtomScale(Number(q.get("size")));
        if (q.get("box") && Number(q.get("box")) > 0) workspace.setBox(Number(q.get("box")));
        workspace.open(cls, Number(q.get("r")));
        if (q.has("cmp")) {
          /* Consume cmp=ids once (from a flag or a shared link), so returning to this class does not reapply it. */
          workspace.setCompare(q.get("cmp").split(",").filter(Boolean).map(Number)
            .filter((id) => Number.isInteger(id) && id >= 0 && id < store.reactions.length));
          q.delete("cmp"); writeHash(q);
        }
        workspace.show();
        window.scrollTo(0, 0);
      } else {
        if (workspace) workspace.hide();
        workspaceRoot.hidden = true;
        galleryRoot.hidden = false;
        gallery.setView(q.get("view") === "flags");
        gallery.resume();
      }
    };
    gallery = new SM.Gallery(galleryRoot, store, renderer, (id) => { const q = hashState(); q.set("c", id); q.delete("r"); location.hash = q.toString(); });
    if (hashState().get("missed") === "1") gallery.showMissed();
    SM.app = { store, gallery, renderer, workspace: null, route, h };
    window.addEventListener("hashchange", route);
    status.remove();
    route();
  } catch (error) {
    status.textContent = `This file could not be opened: ${error.message}`;
    status.classList.add("error");
    throw error;
  }
})();

"""Export a single offline geometry viewer for missed reference saddle groups."""
from base64 import b64encode
from collections import defaultdict
from gzip import compress
from html import escape
import json

from ase.geometry import find_mic
import numpy as np

from saddlebench.scoring.uma import GEOMETRY_COLUMNS
from saddlebench.uma_metrics import METHOD_LABELS


def endpoint_pairs(scoring, parent, requested, saddle_ids):
    pairs = defaultdict(list)
    by_saddle = defaultdict(set)
    for mid, sid in requested:
        by_saddle[sid].add(mid)
    for pair in scoring.dataset.table("endpoint_pairs", filters=[("parent_id", "=", parent)]).to_pylist():
        sid = pair["saddle_id"]
        a, b = pair["minimum_a"], pair["minimum_b"]
        if sid not in saddle_ids or a == b or not pair["n_complete"]:
            continue
        for link in pair["seed_barriers"]:
            mid = link["minimum_id"]
            if mid not in by_saddle[sid]:
                continue
            canonical = scoring.seeds[mid].get("canonical_id") or mid
            ea, eb = pair["energy_a_eV"], pair["energy_b_eV"]
            difference = eb - ea if ea is not None and eb is not None else None
            if canonical == a:
                pairs[mid, sid].append((b, eb, difference))
            elif canonical == b:
                pairs[mid, sid].append((a, ea, -difference if difference is not None else None))
    return pairs


def geometry_payload(scoring, parent, cases):
    requested = {(mid, sid) for mid, methods in cases.items()
                 for thresholds in methods.values() for ids in thresholds.values() for sid in ids}
    if not requested:
        return
    saddle_ids = {sid for _, sid in requested}
    pairs = endpoint_pairs(scoring, parent, requested, saddle_ids)
    chosen = {key: min(options, key=lambda pair: (pair[1] is None, pair[1] or 0, pair[0]))
              for key, options in pairs.items()}
    product_ids = {pair[0] for pair in chosen.values()}
    records = {mid: scoring.seeds[mid] for mid in cases}
    for table, id_column, identifiers in (
        ("saddles", "saddle_id", saddle_ids), ("minima", "minimum_id", product_ids),
    ):
        if identifiers:
            records.update({
                row[id_column]: row for row in scoring.dataset.table(
                    table, columns=[id_column, *GEOMETRY_COLUMNS], filters=[("parent_id", "=", parent)]
                ).to_pylist() if row[id_column] in identifiers
            })
    frames = dict(scoring.dataset.frames(records))
    for mid, methods in cases.items():
        seed = frames[mid]
        entries = {}
        for sid in sorted({key[1] for key in requested if key[0] == mid}):
            saddle = frames[sid]
            move, distances = find_mic(saddle.positions - seed.positions, seed.cell, seed.pbc)
            product = chosen.get((mid, sid))
            entry = {
                "barrier": scoring.references[mid][sid]["barrier_eV"],
                "maxMove": round(float(distances.max()), 3),
                "move": np.round(move, 3).tolist(),
                "endpointCount": len({choice[0] for choice in pairs[mid, sid]}),
            }
            if product and product[0] in frames:
                step, _ = find_mic(
                    frames[product[0]].positions - saddle.positions, seed.cell, seed.pbc
                )
                entry.update(product=product[0], step=np.round(step, 3).tolist())
                if product[2] is not None:
                    entry["productEnergy"] = round(product[2], 4)
            entries[sid] = entry
        if entries:
            yield scoring.seeds[mid]["case_id"], {
                "numbers": seed.numbers.tolist(), "positions": np.round(seed.positions, 3).tolist(),
                "methods": methods, "saddles": entries,
            }


def write_missed_viewer(path, scoring, matched):
    by_parent = defaultdict(lambda: defaultdict(dict))
    menu = []
    for (mid, method), hit_ids in matched.items():
        eligible = scoring.references[mid]
        seed = scoring.seeds[mid]
        by_parent[seed["parent_id"]][mid][method] = {
            threshold: sorted(eligible.keys() - set(ids), key=lambda sid: (eligible[sid]["barrier_eV"], sid))
            for threshold, ids in hit_ids.items()
        }
    with path.open("x", encoding="utf-8") as output:
        output.write(HEAD)
        for parent, cases in by_parent.items():
            for case, payload in geometry_payload(scoring, parent, cases):
                menu.append((case, scoring.datasets[parent], parent))
                packed = b64encode(compress(json.dumps(payload, separators=(",", ":")).encode())).decode()
                output.write(f'<script type="application/octet-stream" id="frames-{escape(case)}">{packed}</script>\n')
        labels = {**METHOD_LABELS, "submission": "Your submission"}
        index = json.dumps({"cases": sorted(menu), "labels": labels}).replace("<", "\\u003c")
        output.write(f'<script type="application/json" id="index">{index}</script>\n')
        output.write(SCRIPT)


HEAD = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Missed saddles · SaddleBench UMA</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;color:#182829;background:#f2f5f2;margin:0}
main{max-width:76rem;margin:auto;padding:clamp(1rem,4vw,3rem)}
h1{margin:0;font-size:clamp(1.8rem,4vw,3rem)} p{margin:.5rem 0}
.controls{display:flex;gap:.8rem;flex-wrap:wrap;margin:1rem 0}
label{display:grid;gap:.3rem;font-weight:650}
select,input{font:inherit;max-width:100%;padding:.4rem}
section{background:#fff;border:1px solid #cddbd3;border-radius:.6rem;padding:1rem}
canvas{display:block;width:100%;height:clamp(16rem,55vh,35rem);touch-action:none;cursor:grab}
#path{width:100%;max-width:630px}
#energy{fill:#275c4c}
#details{overflow-wrap:anywhere}
[hidden]{display:none}
</style></head><body><main>
<h1>Missed reference saddles</h1>
<p>Choose a dataset, case, method and cutoff. Saddles are ordered by barrier.</p>
<div class="controls">
<label>Dataset<select id="dataset"></select></label>
<label>Case<select id="case"></select></label>
<label>Method<select id="method"></select></label>
<label>Cutoff<select id="cutoff"><option value="0.20">0.20 Å</option><option value="0.10">0.10 Å</option><option value="0.05">0.05 Å</option></select></label>
<label>Missed saddle<select id="saddle"></select></label>
</div>
<p id="status" aria-live="polite"></p>
<section id="viewer" hidden>
<p id="details"></p>
<canvas id="atoms" aria-label="Interpolated atom positions. Drag to rotate."></canvas>
<label>Geometry interpolation
<input id="position" type="range" min="0" max="2" step="0.01" value="1"></label>
<p id="stage"></p>
<svg id="path" viewBox="0 0 630 105" aria-label="Stored energies at the seed, saddle and product">
<polyline id="energy-line" fill="none" stroke="#367b65" stroke-width="3" stroke-dasharray="5 4"/>
<text id="energy" x="10" y="98"></text></svg>
<p>Orange marks atoms moving at least 0.2 Å toward the saddle. Drag to rotate. For multiple seed-connected products, the viewer uses the lowest stored product energy. The interpolation joins the exported geometries and does not represent a computed reaction path.</p>
</section>
"""

SCRIPT = """<script>
const index = JSON.parse(document.querySelector('#index').textContent);
const get = id => document.getElementById(id);
const dataset = get('dataset'), cases = get('case'), method = get('method');
const cutoff = get('cutoff'), saddle = get('saddle'), position = get('position');
const canvas = get('atoms'), ctx = canvas.getContext('2d');
let payload, selected, azimuth = 0.35, elevation = -0.6, currentRequest = 0;
function options(element, values, previous = element.value) {
  element.replaceChildren(...values.map(([key, label]) => new Option(label, key)));
  if (values.some(([key]) => key === previous)) element.value = previous;
}
const datasets = [...new Set(index.cases.map(row => row[1]))].sort();
options(dataset, [['all', 'All datasets'], ...datasets.map(name => [name, name])]);
function updateCases() {
  options(cases, index.cases.filter(row => dataset.value === 'all' || row[1] === dataset.value)
    .map(([id, , name]) => [id, id + ' · ' + name.split(':').at(-1).slice(0, 28)]));
  loadCase();
}
async function loadCase() {
  const token = ++currentRequest;
  get('viewer').hidden = true;
  if (!cases.value) {
    get('status').textContent = 'No missed reference saddles in this report.';
    return;
  }
  get('status').textContent = 'Loading geometries…';
  try {
    const packed = atob(get('frames-' + cases.value).textContent);
    const bytes = Uint8Array.from(packed, ch => ch.charCodeAt(0));
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    const decoded = await new Response(stream).json();
    if (token !== currentRequest) return;
    payload = decoded;
    options(method, Object.keys(payload.methods).map(key => [key, index.labels[key] || key]));
    updateSaddles();
  } catch {
    if (token === currentRequest) get('status').textContent =
      'Could not load the embedded geometries. Open this file in a current browser.';
  }
}
function updateSaddles() {
  const ids = payload?.methods[method.value]?.[cutoff.value] || [];
  options(saddle, ids.map(key => [key, payload.saddles[key].barrier.toFixed(3) + ' eV · ' + key]));
  get('status').textContent = ids.length + ' missed saddle groups at ' + cutoff.value + ' Å';
  showSelected();
}
function showSelected() {
  selected = payload?.saddles[saddle.value];
  get('viewer').hidden = !selected;
  if (!selected) return;
  position.max = selected.step ? 2 : 1;
  position.value = 1;
  const endpoint = selected.product
    ? 'Product ' + selected.product + ' (' + selected.endpointCount + ' eligible product choices)'
    : 'Product geometry unavailable';
  get('details').textContent = 'Barrier ' + selected.barrier.toFixed(3) + ' eV · maximum seed-to-saddle displacement '
    + selected.maxMove.toFixed(3) + ' Å · ' + endpoint;
  const energy = [0, selected.barrier];
  if (selected.productEnergy !== undefined) energy.push(selected.productEnergy);
  const low = Math.min(...energy), span = Math.max(Math.max(...energy) - low, 0.01);
  get('energy-line').setAttribute('points', energy.map((e,i) => [35 + 270*i, 76 - 64*(e-low)/span].join(',')).join(' '));
  get('energy').textContent = 'Seed 0.000 eV · Saddle ' + selected.barrier.toFixed(3) + ' eV' +
    (energy.length > 2 ? ' · Product ' + energy[2].toFixed(3) + ' eV' : '');
  draw();
}
function draw() {
  if (!selected || get('viewer').hidden) return;
  const phase = Number(position.value), first = Math.min(phase, 1), second = Math.max(0, phase - 1);
  get('stage').textContent = phase === 0 ? 'Starting minimum' : phase === 1 ? 'Saddle' :
    phase === 2 ? 'Product minimum' : phase < 1 ? 'Starting minimum → saddle' : 'Saddle → product minimum';
  const size = canvas.getBoundingClientRect(), scale = devicePixelRatio || 1;
  canvas.width = Math.round(size.width * scale);
  canvas.height = Math.round(size.height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  const center = [0, 1, 2].map(j => payload.positions.reduce((acc, p) => acc+p[j], 0) / payload.positions.length);
  const extent = Math.max(1, ...payload.positions.map(p => Math.hypot(...p.map((x,j) => x-center[j])))) + 2;
  const zoom = 0.44 * Math.min(size.width, size.height) / extent;
  const ca = Math.cos(azimuth), sa = Math.sin(azimuth);
  const cb = Math.cos(elevation), sb = Math.sin(elevation);
  const points = payload.positions.map((p,i) => {
    const [x,y,z] = p.map((v,j) => v - center[j] + first*selected.move[i][j] + second*(selected.step?.[i][j] || 0));
    const u = ca*x - sa*y, v = sa*x + ca*y;
    return [u, cb*v-sb*z, sb*v+cb*z, i];
  }).sort((a,b) => a[2]-b[2]);
  for (const [u,v,z,i] of points) {
    const moving = Math.hypot(...selected.move[i]) >= 0.2;
    ctx.fillStyle = moving ? '#cf4d1f' : '#497872';
    ctx.globalAlpha = moving ? 1 : 0.38 + 0.28*(z+extent)/(2*extent);
    ctx.beginPath();
    ctx.arc(size.width/2+u*zoom, size.height/2-v*zoom, Math.max(2, zoom*(moving ? .29 : .23)), 0, 2*Math.PI);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}
let drag;
canvas.addEventListener('pointerdown', e => { drag = [e.clientX,e.clientY]; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener('pointermove', e => {
  if (!drag) return;
  azimuth += (e.clientX-drag[0])*.01;
  elevation += (e.clientY-drag[1])*.01;
  drag = [e.clientX,e.clientY];
  draw();
});
canvas.addEventListener('lostpointercapture', () => { drag = null; });
window.addEventListener('resize', draw);
dataset.addEventListener('change', updateCases);
cases.addEventListener('change', loadCase);
method.addEventListener('change', updateSaddles);
cutoff.addEventListener('change', updateSaddles);
saddle.addEventListener('change', showSelected);
position.addEventListener('input', draw);
updateCases();
</script></main></body></html>
"""

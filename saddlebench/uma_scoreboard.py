"""Standalone report for UMA seed catalogs and scoring results."""
from html import escape
from collections import defaultdict
from statistics import mean
from urllib.parse import quote
import json

from saddlebench.reference.uma import CONFIDENCE_TAIL, REVISION, confidence_sources
from saddlebench.scoring.uma import THRESHOLDS
from saddlebench.uma_catalog import select_cases
from saddlebench.uma_metrics import METHOD_LABELS, TEMPERATURE_K
from saddlebench.explorer import write_explorer


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SaddleBench UMA scoreboard</title>
<style>
:root { color-scheme: light; font: 16px/1.5 system-ui, sans-serif; color: #182829; background: #f2f5f2; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 92rem; margin: auto; padding: clamp(1rem, 4vw, 3.5rem); }
header { border-bottom: 3px solid #367b65; padding-bottom: 1.4rem; }
h1 { margin: 0; font-size: clamp(2rem, 5vw, 3.6rem); line-height: 1.1; letter-spacing: -.035em; }
h2 { margin: 0 0 .25rem; font-size: 1.3rem; }
p { margin: .35rem 0; }
small, .muted { color: #435b59; }
.controls { display: flex; flex-wrap: wrap; align-items: end; gap: 1rem 1.5rem; margin: 1.4rem 0; }
label { display: grid; gap: .3rem; font-weight: 650; }
select { font: inherit; padding: .45rem .65rem; background: white; color: inherit; border: 1px solid #7f9990; border-radius: .35rem; }
select:focus-visible, a:focus-visible, [tabindex]:focus-visible { outline: 3px solid #267456; outline-offset: 3px; }
section { background: #fff; border: 1px solid #cddbd3; border-radius: .65rem; margin: 1rem 0; padding: clamp(.8rem, 2.5vw, 1.5rem); }
.scroll { overflow: auto; max-height: 35rem; margin-top: .8rem; }
table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: .9rem; }
th, td { padding: .55rem .65rem; text-align: left; border-bottom: 1px solid #e0e9e3; }
th { position: sticky; top: 0; background: #eaf3ee; white-space: nowrap; }
td.number { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tbody tr:hover { background: #f2f8f4; }
.eligible { color: #1a6045; font-weight: 650; }
.below { color: #645744; }
a { color: #1a6045; }
code { overflow-wrap: anywhere; }
summary { cursor: pointer; font-weight: 650; }
details[open] summary { margin-bottom: .8rem; }
td.identity { max-width: 20rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
[data-scope] td:first-child { font-weight: 650; }
#coverage-chart { width: 100%; max-width: 60rem; }
#coverage-chart text { fill: #435b59; font-size: 14px; }
#coverage-legend { display: flex; flex-wrap: wrap; gap: .6rem 1.1rem; }
[hidden] { display: none !important; }
</style>
</head>
<body>
<main>
<header>
<h1>SaddleBench UMA scoreboard</h1>
"""

FILTER = """<script>
const dataset = document.querySelector('#dataset');
const confidence = document.querySelector('#confidence');
const catalog = [...document.querySelectorAll('#seeds tbody tr')];
const results = [...document.querySelectorAll('#results tbody tr')];
const aggregates = [...document.querySelectorAll('[data-scope]')];
function inDataset(row) {
  const name = row.dataset.dataset;
  return dataset.value === 'all' || name === dataset.value ||
    (dataset.value === 'literature' && name.startsWith('lit_'));
}
function update() {
  const selected = catalog.filter(inDataset);
  const eligible = selected.filter(row => row.dataset.confidence === 'eligible').length;
  let seedCount = 0;
  let visibleResults = 0;
  for (const row of catalog) {
    row.hidden = !inDataset(row) ||
      (confidence.value !== 'all' && row.dataset.confidence !== confidence.value);
    if (!row.hidden) seedCount += 1;
  }
  for (const row of results) {
    row.hidden = !inDataset(row);
    if (!row.hidden) visibleResults += 1;
  }
  for (const row of aggregates) row.hidden = row.dataset.scope !== dataset.value;
  document.querySelector('#counts').textContent =
    eligible + ' of ' + selected.length + ' seeds eligible in this selection';
  document.querySelector('#seed-count').textContent = seedCount + ' seeds shown';
  document.querySelector('#result-count').textContent = visibleResults
    ? visibleResults + ' result rows shown'
    : 'No results in this report for the selected dataset.';
  document.querySelector('#results').hidden = !visibleResults;
  updateCoverageChart();
}
function updateCoverageChart() {
  const data = document.querySelector('#coverage-data');
  if (!data) return;
  const graph = document.querySelector('#coverage-lines');
  const legend = document.querySelector('#coverage-legend');
  const key = document.querySelector('#coverage-weight').value + ':' +
    document.querySelector('#coverage-cutoff').value;
  const series = JSON.parse(data.textContent)[dataset.value]?.[key] || [];
  const span = Math.max(1, ...series.map(line => line.duration));
  graph.replaceChildren();
  legend.replaceChildren();
  const colors = {'sella_prfo_diag4_abs01': '#266c50',
    'tsbfgs_dimer_rot4_frot01_r23_allprobes': '#164b94',
    'sella_motif_augmented': '#a34b15', 'tsbfgs_dimer_motif_augmented': '#684596', 'submission': '#b42318'};
  for (const line of series) {
    const color = colors[line.id];
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const x = t => (56 + 687 * t / span).toFixed(2);
    const y = f => (246 - 205 * Math.min(1, f)).toFixed(2);
    const points = line.points.map(([t,f]) => 'H' + x(t) + 'V' + y(f)).join('');
    path.setAttribute('d', 'M56 246' + points + 'H' + x(line.duration));
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', '2.5');
    graph.append(path);
    const label = document.createElement('span');
    label.style.color = color;
    label.textContent = '● ' + line.label + ' (' + line.structures + ' timed structures)';
    legend.append(label);
  }
  if (!series.length) legend.textContent = 'No complete timing data in this selection.';
  document.querySelector('#chart-end').textContent = (span / 3600).toPrecision(3) + ' h';
}
dataset.addEventListener('change', update);
confidence.addEventListener('change', update);
document.querySelector('#coverage-weight')?.addEventListener('change', updateCoverageChart);
document.querySelector('#coverage-cutoff')?.addEventListener('change', updateCoverageChart);
update();
</script>
</main>
</body>
</html>
"""


def cell(value, *, numeric=False):
    attr = ' class="number"' if numeric else ""
    return f"<td{attr}>{escape(str(value)) if value is not None else '—'}</td>"


def result_row(row, *, baseline, pages=None, labels=METHOD_LABELS):
    fields = [cell(row["dataset"])]
    if not baseline:
        fields.append(cell(row["case_id"]))
    page = (pages or {}).get(row["parent_id"])
    method = row.get("method_id", "submission")
    href = f"{page}#m={quote(method)}&missed=1" if page else None
    fields.append(structure_cell(row["parent_id"], href))
    if baseline:
        fields.append(cell(labels.get(row["method_id"], row["method_id"])))
        fields.extend(cell(row.get(key), numeric=True) for key in ("attempts", "converged"))
    fields.append(cell(row["predictions_scored"], numeric=True))
    eligible = row["eligible_reference_groups"]
    for threshold in THRESHOLDS:
        found = row[f"reference_groups_found_{threshold:.2f}_A"]
        fields.append(cell(f"{found}/{eligible} ({found / eligible:.1%})" if eligible else None, numeric=True))
    fields.append(cell(row["outside_reference_predictions"], numeric=True))
    cost = row.get("total_benchmark_work_s")
    fields.append(cell(f"{cost / 3600:,.2f} h" if cost is not None else "N/A", numeric=True))
    return f'<tr data-dataset="{escape(row["dataset"])}">{"".join(fields)}</tr>'


def structure_cell(name, href=None):
    shown = name.split(":", 1)[-1]
    if len(shown) > 30:
        shown = shown[:29].rstrip("_") + "…"
    if href:
        return (f'<td class="identity" title="{escape(name)}: open in the explorer with missed saddles shown">'
                f'<a href="{escape(href, quote=True)}">{escape(shown)}</a></td>')
    return f'<td class="identity" title="{escape(name)}">{escape(shown)}</td>'


def coverage_values(rows, cutoff, *, rate):
    found_key = f"found_rate_weight_{cutoff:.2f}_A" if rate else f"reference_groups_found_{cutoff:.2f}_A"
    total_key = "eligible_rate_weight" if rate else "eligible_reference_groups"
    pairs = [(row[found_key], row[total_key]) for row in rows if row[total_key]]
    if not pairs:
        return None, None
    return mean(hits / total for hits, total in pairs), (
        sum(hits for hits, _ in pairs) / sum(total for _, total in pairs)
    )


def method_summary(rows, options, selected, labels=METHOD_LABELS):
    tables = []
    base_headers = ["Method", "Structures", "Reference hits at 0.20 Å", "Total search work"]
    for rate, title in ((True, "Rate coverage"), (False, "Unweighted group coverage")):
        headers = "".join(f'<th scope="col">{label}</th>' for label in
                          base_headers + [f"{cutoff:.2f} Å" for cutoff in THRESHOLDS])
        table = [f'<h2>{title}</h2><div class="scroll"><table><thead><tr>{headers}</tr></thead><tbody>']
        for scope, _ in options:
            per_method = defaultdict(list)
            for row in rows:
                if scope == "all" or row["dataset"] == scope or (
                    scope == "literature" and row["dataset"].startswith("lit_")
                ):
                    per_method[row["method_id"]].append(row)
            ordered = sorted(labels, key=lambda method: (
                not per_method[method],
                -(coverage_values(per_method[method], THRESHOLDS[0], rate=rate)[0] or 0),
            ))
            for method in ordered:
                group = per_method[method]
                hits = sum(row["reference_groups_found_0.20_A"] for row in group)
                eligible = sum(row["eligible_reference_groups"] for row in group)
                times = [row["total_benchmark_work_s"] for row in group]
                hours = sum(times) / 3600 if times and None not in times else None
                fields = [cell(labels[method]), cell(len(group), numeric=True),
                          cell(f"{hits}/{eligible}" if group else "No results"),
                          cell(f"{hours:,.2f} h" if hours is not None else "N/A", numeric=True)]
                for cutoff in THRESHOLDS:
                    equal, pooled = coverage_values(group, cutoff, rate=rate)
                    fields.append(cell(f"{equal:.1%} / {pooled:.1%}" if equal is not None else "N/A", numeric=True))
                hidden = "" if scope == selected else " hidden"
                table.append(f'<tr data-scope="{escape(scope)}"{hidden}>{"".join(fields)}</tr>')
        table.append("</tbody></table></div>")
        tables.append("".join(table))
    return f"""<section aria-label="Overall benchmark">
<p>Each coverage cell shows <strong>equal weight per structure / pooled across structures</strong>.</p>
<p class="muted">Rate coverage uses exp(−barrier / k<sub>B</sub>T) at {TEMPERATURE_K} K with a common prefactor.
Unweighted coverage gives every eligible saddle group equal weight within its structure.
Compare rows on the same structures. Motif rows require additional proposals.</p>
{''.join(tables)}
</section>"""


def coverage_chart(rows, options, timelines, labels=METHOD_LABELS):
    if not timelines:
        return ""
    data = {}
    for scope, _ in options:
        curves = defaultdict(list)
        selected = defaultdict(list)
        for row in rows:
            if scope not in ("all", row["dataset"]) and not (
                scope == "literature" and row["dataset"].startswith("lit_")
            ):
                continue
            trace = timelines.get((row["parent_id"], row["method_id"]))
            if trace is not None and row["eligible_reference_groups"]:
                selected[row["method_id"]].append((row, trace))
        common = set.intersection(*(
            {row["parent_id"] for row, _ in group} for group in selected.values()
        )) if selected else set()
        for method, group in selected.items():
            group = [(row, trace) for row, trace in group if row["parent_id"] in common]
            if not group:
                continue
            duration = max(trace[0] for _, trace in group)
            for index, cutoff in enumerate(THRESHOLDS):
                for metric in ("rate", "count"):
                    timed = sorted(
                        (time, (weight / row["eligible_rate_weight"] if metric == "rate" else
                                1 / row["eligible_reference_groups"]) / len(group))
                        for row, (_, events) in group for time, event_index, weight in events
                        if event_index == index
                    )
                    points = []
                    covered = 0.
                    for time, increment in timed:
                        covered += increment
                        points.append([round(time, 3), round(covered, 7)])
                    curves[f"{metric}:{cutoff:.2f}"].append(dict(
                        id=method, label=labels.get(method, method), duration=duration,
                        structures=len(group), points=points,
                    ))
        data[scope] = curves
    packed = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<section aria-labelledby="coverage-title">
<h2 id="coverage-title">Observed coverage versus time</h2>
<p class="muted">Equal weight on structures with timing data for every plotted method. The time axis sums reported attempt durations in
attempt order, including unsuccessful attempts, and adds the SaddleMotif stage as one batch.
It represents serial-equivalent work. The lines end at the measured budget.</p>
<div class="controls">
<label>Coverage<select id="coverage-weight"><option value="rate">Rate at {TEMPERATURE_K} K</option>
<option value="count">Unweighted groups</option></select></label>
<label>Cutoff<select id="coverage-cutoff"><option>0.20</option><option>0.10</option><option>0.05</option></select></label>
</div>
<svg id="coverage-chart" viewBox="0 0 760 284" role="img" aria-label="Coverage by time">
<path d="M56 41H743 M56 144H743 M56 246H743 M56 41V246" fill="none" stroke="#a3b8af"/>
<text x="4" y="46">100%</text><text x="10" y="149">50%</text><text x="20" y="251">0%</text>
<text x="56" y="270">0 h</text><text x="743" y="270" text-anchor="end" id="chart-end"></text>
<g id="coverage-lines"></g></svg>
<div id="coverage-legend" aria-live="polite"></div>
<script type="application/json" id="coverage-data">{packed}</script>
</section>"""


def seed_row(scoring, seed):
    name = scoring.datasets[seed["parent_id"]]
    sources = confidence_sources(seed)
    status = "eligible" if sources else "below"
    confidence = "Eligible · " + " + ".join(sources) if sources else "Below threshold"
    eligible = len(scoring.references[seed["minimum_id"]]) if sources or scoring.include_unconfident else None
    return (
        f'<tr data-dataset="{escape(name)}" data-confidence="{status}">'
        f'{cell(seed["case_id"])}{cell(name)}{structure_cell(seed["parent_id"])}'
        f'<td class="{status}">{escape(confidence)}</td>'
        f'{cell(seed.get("actual_random_tail"), numeric=True)}{cell(eligible, numeric=True)}</tr>'
    )


def write_scoreboard(path, scoring, rows, *, dataset, baseline=False, matched=None, timelines=None,
                     labels=None, cache_dir=None, explorer_payloads=None):
    """Write the scoreboard HTML and, when matches are given, a structure explorer beside it (STEM-explorer/)."""
    labels = dict(labels or METHOD_LABELS)
    seeds = select_cases(scoring, confident_only=False)
    eligible = sum(bool(confidence_sources(seed)) for seed in seeds)
    names = sorted({scoring.datasets[seed["parent_id"]] for seed in seeds})
    options = [("all", "All datasets"), ("literature", "All literature datasets")]
    options.extend((name, name) for name in names)
    option_html = "".join(
        f'<option value="{escape(value)}"{" selected" if value == dataset else ""}>{escape(label)}</option>'
        for value, label in options
    )
    headers = (
        ["Dataset", "Structure", "Method", "Attempts", "Converged"] if baseline
        else ["Dataset", "Case", "Structure"]
    )
    headers += ["Predictions compared", *[f"Found at {t:.2f} Å" for t in THRESHOLDS], "Unmatched frames"]
    headers += ["Search work"]
    result_headers = "".join(f'<th scope="col">{name}</th>' for name in headers)
    pages, missed_link = {}, ""
    if matched:
        explorer = path.with_name(path.stem + "-explorer")
        pages = write_explorer(explorer, scoring, matched, labels, cache_dir=cache_dir,
                               payload_dir=explorer_payloads, scoreboard_href=f"../{quote(path.name)}")
        pages = {parent: f"{quote(explorer.name)}/{quote(page)}" for parent, page in pages.items()}
        if pages:
            missed_link = (f'<section class="explore"><h2><a href="{quote(explorer.name)}/index.html">Explore the scored '
                           f'structures</a></h2><p class="muted">Every structure as a picture, its saddle classes and '
                           f'reactions, with reference saddles marked found or missed for each method. Structure '
                           f'names in the results table open that structure with its misses shown.</p></section>')
    results_html = "\n".join(result_row(row, baseline=baseline, pages=pages, labels=labels) for row in rows)
    seeds_html = "\n".join(seed_row(scoring, seed) for seed in seeds)
    summary = method_summary(rows, options, dataset, labels) if baseline else ""
    chart = coverage_chart(rows, options, timelines, labels)
    with path.open("x", encoding="utf-8") as output:
        output.write(PAGE)
        output.write(f"""
<p>{eligible} of {len(seeds)} published seeds meet the benchmark confidence rule.</p>
<p class="muted">A seed qualifies if its actual or shuffled mean repeat tail reaches {CONFIDENCE_TAIL}.</p>
<p class="muted">{'This run includes seeds below confidence.' if scoring.include_unconfident else 'This run uses confident seeds.'}</p>
</header>
<div class="controls">
<label for="dataset">Dataset<select id="dataset">{option_html}</select></label>
<label for="confidence">Seeds<select id="confidence">
<option value="all">All confidence statuses</option>
<option value="eligible">Eligible only</option>
<option value="below">Below threshold</option>
</select></label>
<p id="counts" aria-live="polite"></p>
</div>
{summary}
{chart}
{missed_link}
<section aria-labelledby="results-title">
<details{" open" if not baseline else ""}>
<summary id="results-title">{"Per-structure results" if baseline else "Submission results"}</summary>
<p class="muted" id="result-count" aria-live="polite">{len(rows)} result rows shown</p>
<p class="muted">Predictions compared includes matches and non-matches.</p>
<div id="results" class="scroll" tabindex="0" role="region" aria-label="Scoring results">
<table><thead><tr>{result_headers}</tr></thead><tbody>{results_html}</tbody></table>
</div>
</details>
</section>
<section aria-labelledby="seeds-title">
<h2 id="seeds-title">Seed catalog</h2>
<p class="muted" id="seed-count" aria-live="polite">{len(seeds)} seeds shown</p>
<div id="seeds" class="scroll" tabindex="0" role="region" aria-label="Published seed catalog">
<table><thead><tr>
<th scope="col">Case</th><th scope="col">Dataset</th><th scope="col">Structure</th>
<th scope="col">Confidence</th><th scope="col">Actual repeat tail</th><th scope="col">Eligible groups</th>
</tr></thead><tbody>{seeds_html}</tbody></table>
</div>
</section>
<section aria-labelledby="submit-title">
<h2 id="submit-title">Score your own search</h2>
<p>Download a seed with <code>saddlebench seed --case-id CASE_ID --output seed.traj</code>.
Run your search and save one saddle per frame, preserving atom order, cell, and periodic boundaries.</p>
<p><code>saddlebench score --case-id CASE_ID --submission predictions.traj --html score.html</code></p>
<p class="muted">Reference revision <a href="https://huggingface.co/datasets/traingletrain/saddlebench-uma/tree/{REVISION}"><code>{REVISION}</code></a></p>
</section>
""")
        output.write(FILTER)

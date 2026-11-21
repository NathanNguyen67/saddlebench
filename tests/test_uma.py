import hashlib
import json
from pathlib import Path

from ase import Atoms
from ase.io import write
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saddlebench.public_entrypoint import main
from saddlebench.reference.uma import REPO, REVISION, UmaDataset
from saddlebench.scoring.uma import SeedScorer, geometry


@pytest.fixture
def export(tmp_path, monkeypatch, request):
    mode = getattr(request, 'param', True)
    confident = mode is True
    actual_tail = 200 if mode == 'actual' else 0
    root = tmp_path / "export"
    root.mkdir()
    shard = root / "geometries" / "structure0001.traj"
    shard.parent.mkdir()
    seed = Atoms("H4", positions=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    seed.info["saddlebench_id"] = "seed"
    saddle = seed.copy()
    saddle.positions[-1, 2] += .2
    saddle.info["saddlebench_id"] = "saddle"
    other = saddle.copy()
    other.info["saddlebench_id"] = "other"
    other.positions[0, 0] += 1.
    product = saddle.copy()
    product.info["saddlebench_id"] = "product"
    product.positions[-1, 2] += .2
    write(shard, [seed, saddle, other, product])
    write(tmp_path / "predictions.traj", [saddle, other, saddle, other])
    write(tmp_path / "predictions.extxyz", [saddle, other, saddle, other])
    locations = {"geometry_path": "geometries/structure0001.traj"}
    case = {"case_id": "case0001", "minimum_id": "seed", "parent_id": "parent"}
    (root / "cases.csv").write_text(
        "case_id,minimum_id,parent_id,confident,geometry_path,geometry_index\n"
        f"case0001,seed,parent,{confident},geometries/structure0001.traj,0\n"
    )
    tables = {
        "seeds": [{**case, **locations, "geometry_index": 0, "confident": confident,
                   "actual_random_tail": actual_tail}],
        "saddles": [{**locations, "parent_id": "parent", "saddle_id": sid, "geometry_index": i}
                    for i, sid in [(1, "saddle"), (2, "other")]],
        "reference": [{**case, "saddle_id": sid, "barrier_eV": barrier}
                      for sid, barrier in [("saddle", .99), ("other", 1.0)]],
        "structures": [{"parent_id": "parent", "dataset": "lit_metal_surfaces"}],
        "minima": [{**locations, "minimum_id": "product", "parent_id": "parent", "geometry_index": 3}],
        "endpoint_pairs": [{"parent_id": "parent", "minimum_a": "seed", "minimum_b": "product",
                            "saddle_id": "saddle", "n_complete": 1, "energy_a_eV": 0.,
                            "energy_b_eV": -.2, "seed_barriers": [{"minimum_id": "seed"}]}],
    }
    for name, rows in tables.items():
        pq.write_table(pa.Table.from_pylist(rows), root / f"{name}.parquet")
    manifest = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*") if path.is_file()}
    (root / "MANIFEST.json").write_text(json.dumps({"sha256": manifest}))

    def download(repo, name, *, repo_type, revision, cache_dir):
        assert (repo, repo_type, revision) == (REPO, "dataset", REVISION)
        return str(root / name)

    monkeypatch.setattr("saddlebench.reference.uma.hf_hub_download", download)
    return root


@pytest.mark.parametrize("suffix", ["traj", "extxyz"])
def test_public_uma_cli_counts_predictions(export, suffix):
    path = export.parent / f"predictions.{suffix}"
    output = export.parent / "score.json"
    assert main(["score", "--case-id", "case0001", "--submission", str(path),
                 "--output", str(output), "--elapsed-seconds", "42",
                 "--physical-pes-calls", "50"]) == 0
    result = json.loads(output.read_text())
    assert result["eligible_reference_groups"] == 1
    assert result["matched_reference_groups"] == {label: 1 for label in ("0.05", "0.10", "0.20")}
    assert result["recall"]["0.20"] == 1
    assert result["outside_reference_predictions"] == 2
    assert result["outside_reference_groups"] == 1
    assert result["predictions_scored"] == 4
    assert result["reported_search_wall_s"] == 42
    assert result["physical_pes_calls"] == 50
    assert result["dataset"] == "lit_oc20"


@pytest.mark.parametrize("export", [False], indirect=True)
def test_confidence_filter(export, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["score", "--case-id", "case0001", "--submission", "unused.traj"])
    assert exc.value.code == 2
    assert "confidence threshold" in capsys.readouterr().err
    assert main(["cases", "--all-seeds"]) == 0
    assert "case0001,lit_oc20,parent,," in capsys.readouterr().out
    output = export.parent / "below-confidence.json"
    assert main(["score", "--case-id", "case0001", "--submission",
                 str(export.parent / "predictions.traj"), "--include-unconfident",
                 "--output", str(output)]) == 0
    assert json.loads(output.read_text())["matched_reference_groups"]["0.20"] == 1
    assert json.loads(output.read_text())["seed_confident"] is False


def test_uma_html_scoreboard_uses_scores_and_full_catalog(export):
    report = export.parent / "score.html"
    predictions = export.parent / "predictions.traj"
    assert main(["score", "--case-id", "case0001", "--submission", str(predictions),
                 "--recall-only", "--html", str(report)]) == 0
    page = report.read_text()
    assert "1 of 1 published seeds" in page
    assert '<option value="lit_oc20" selected>' in page
    assert 'data-confidence="eligible"' in page
    assert page.count("1/1 (100.0%)") == 3
    assert 'id="results"' in page and 'id="seeds"' in page


def test_uma_rate_coverage_and_equal_structure_average():
    from math import exp

    from ase.units import kB
    from saddlebench.uma_metrics import rate_totals
    from saddlebench.uma_scoreboard import coverage_values

    reference = {"low": {"barrier_eV": .1}, "high": {"barrier_eV": .5}}
    values = rate_totals(reference, {"0.20": ["low"], "0.10": ["high"], "0.05": []})
    low = exp(-.1 / (423 * kB))
    high = exp(-.5 / (423 * kB))
    assert values["eligible_rate_weight"] == pytest.approx(low + high)
    assert values["found_rate_weight_0.20_A"] == pytest.approx(low)
    assert values["found_rate_weight_0.10_A"] == pytest.approx(high)
    assert values["found_rate_weight_0.05_A"] == 0
    rows = [
        {"eligible_reference_groups": 1, "reference_groups_found_0.20_A": 1},
        {"eligible_reference_groups": 9, "reference_groups_found_0.20_A": 0},
    ]
    assert coverage_values(rows, .20, rate=False) == (.5, .1)


def test_uma_first_hit_clock_includes_failed_attempts_and_motif_time():
    from saddlebench.uma_metrics import first_hit_events

    attempts = [{"seq": i, "total_wall_s": time} for i, time in enumerate([2., 3., 5., 7.])]
    matches = [{"identity_id": key, "maxdev_angstrom": dist} for key, dist in
               [("a", .09), ("a", .04), ("b", .18)]]
    reference = {key: {"barrier_eV": .2} for key in ("a", "b")}
    total, trace = first_hit_events(attempts, attempts[1:3], matches, reference, proposal_time=11.)
    assert total == 28
    assert [(time, cutoff) for time, cutoff, _ in trace] == [(5, 0), (5, 1), (10, 2), (28, 0)]
    assert first_hit_events(attempts, attempts[1:3], matches, reference) is None


def test_published_motif_download_is_verified_and_reused(tmp_path, monkeypatch):
    import gzip
    from io import BytesIO

    from saddlebench.reference import uma_motif

    expected = b"SaddleMotif geometry bytes"
    record = {"case_id": "case0001", "method_id": "sella", "sha256": hashlib.sha256(expected).hexdigest()}
    calls = []

    def open_test(url, timeout):
        calls.append(url)
        return BytesIO(gzip.compress(expected))

    monkeypatch.setattr(uma_motif, "HF_HUB_OFFLINE", False)
    monkeypatch.setattr(uma_motif, "urlopen", open_test)
    for _ in range(2):
        uma_motif.cache_proposals(record, revision="dummy", directory=tmp_path)
    assert (tmp_path / "sella/case0001.traj").read_bytes() == expected
    assert len(calls) == 1


def test_baseline_selects_eligible_cases_before_loading_default_motif(export, monkeypatch):
    from saddlebench import uma_baseline

    output = export.parent / "baseline.csv"
    proposal_dir = export.parent / "published"
    proposal_dir.mkdir()

    def default_motif(cache_dir, cases):
        assert cases == {"case0001"}
        return proposal_dir

    def score(scoring, attempts, *args, motif_dir, **kwargs):
        assert motif_dir == proposal_dir
        return [{
            "method_id": uma_baseline.METHODS[0], "geometry_source": "test",
            "attempts": 1, "converged": 1, "predictions_scored": 1,
            "converged_without_exported_geometry": 0,
        }]

    monkeypatch.setattr(uma_baseline, "default_motif_dir", default_motif)
    monkeypatch.setattr(uma_baseline, "load_attempts", lambda *args: None)
    monkeypatch.setattr(uma_baseline, "summarize", score)
    assert main(["baseline", "--output", str(output)]) == 0
    assert output.is_file()


def test_uma_motif_bonus(export):
    from ase.io import read
    from saddlebench.scoring.uma import UmaScoring
    from saddlebench.uma_baseline import METHODS, score_structure

    scoring = UmaScoring(UmaDataset(export.parent / "cache"))
    class Search:
        def geometries(self, parent):
            return {1: geometry(read(export.parent / "predictions.traj", index=1), arrays=True)}

    method = METHODS[0]
    proposals = export.parent / "proposals"
    (proposals / method).mkdir(parents=True)
    write(proposals / method / "case0001.traj", read(export.parent / "predictions.traj", index=0))
    rows = score_structure(("parent", {("seed", method): [{
        "seq": 1, "converged": True, "physical_pes_calls": 10, "total_wall_s": 1.,
    }]}), scoring=scoring, search=Search(), archive=None, group_outside=False,
        motif_dir=proposals, include_matches=True)
    reports = {row["method_id"]: row for row in rows}
    base, augmented = reports[method], reports["sella_motif_augmented"]
    assert base["reference_groups_found_0.20_A"] == 0
    assert augmented["reference_groups_found_0.20_A"] == 1
    assert augmented["predictions_scored"] == 2
    assert augmented["physical_pes_calls_per_reference_group_found"] is None


def _explorer_payload(parent, saddles):
    """Smallest valid saddlemotif-explorer-v2 payload: one atom, one class, one reaction per saddle ID."""
    import base64
    empty = base64.b64encode(b"").decode()
    reactions = [{
        "id": i, "saddle": sid, "key": sid, "reactant": "seed", "product": f"p{i}", "seedConnected": True,
        "nObs": 1, "doubleMins": [], "source": "random", "method": None, "findSeq": None, "curvature": -0.5,
        "eR": 0.0, "eS": 0.5, "eP": 0.1, "barrier": 0.5, "reverseBarrier": 0.4, "reactionEnergy": 0.1,
        "saddleMinima": 2, "productGroup": 0, "classes": ["C001"], "frames": [[0, 0]] * 3, "mode": None,
        "motion": {"label": "local", "moved": 1, "fraction": 1.0, "max_A": 0.5, "resultant": 1.0, "groups": []},
    } for i, sid in enumerate(saddles)]
    return {
        "schema": "saddlemotif-explorer-v2",
        "structure": {"parent": parent, "task": "oc20", "dataset": "lit", "numbers": [13], "positions": [[0, 0, 0]],
                      "cell": [[3, 0, 0], [0, 3, 0], [0, 0, 3]], "pbc": [True, True, True], "fixed": [], "seeds": ["seed"]},
        "provenance": {"snapshot": "test", "generation": 1, "model": "uma", "hessians": "unverified"},
        "thresholds": {"moved_A": 0.5}, "encoding": {"displacementA": 0.001, "modeUnit": 1 / 32000},
        "classes": [{"id": "C001", "representative": 0, "reactions": list(range(len(saddles))), "saddles": len(saddles),
                     "barrierMin": 0.5, "barrierMax": 0.5, "productMinima": 1, "productGroups": 1, "majorProductGroups": 1,
                     "multiEndpointSaddles": 0, "alignedGroups": 0, "movedMedian": 1, "motionCounts": {"local": 1},
                     "tags": ["Local"]}],
        "reactions": reactions,
        "buffers": {"frameIndex": empty, "frameValues": empty, "modeIndex": empty, "modeValues": empty},
    }


def test_explorer_marks_found_and_missed_reference_saddles(export, tmp_path):
    import gzip
    import json
    import re
    from saddlebench.explorer import score_overlay, write_explorer
    from saddlebench.scoring.uma import UmaScoring

    scoring = UmaScoring(UmaDataset(export.parent / "cache"))
    payload = _explorer_payload("parent", ["saddle", "other"])
    labels = {"submission": "Your submission", "sella": "Sella"}
    matched = {("seed", "submission"): {"0.20": ["saddle"], "0.10": [], "0.05": []},
               ("seed", "sella"): {"0.20": [], "0.10": [], "0.05": []}}
    overlay = score_overlay(scoring, "parent", {"seed"}, matched, labels, payload)
    assert set(overlay["reference"]) == {"saddle"}
    assert overlay["found"]["submission"]["0.20"] == ["saddle"]
    assert overlay["found"]["sella"]["0.20"] == []
    assert [m["id"] for m in overlay["methods"]] == ["submission", "sella"]
    assert overlay["absent"] == []

    payloads = tmp_path / "payloads"
    payloads.mkdir()
    (payloads / "parent.json.gz").write_bytes(gzip.compress(json.dumps(payload).encode()))
    pages = write_explorer(tmp_path / "site", scoring, matched, labels, payload_dir=payloads, scoreboard_href="../x.html")
    assert pages == {"parent": "parent.html"}
    page = (tmp_path / "site" / "parent.html").read_text()
    config = json.loads(re.search(r'<script id="page-config" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert config["mode"] == "bench" and config["score"]["found"]["submission"]["0.20"] == ["saddle"]
    index = (tmp_path / "site" / "index.html").read_text()
    data = json.loads(re.search(r'<script id="index-data" type="application/json">(.*?)</script>', index, re.S).group(1))
    [row] = data["rows"]
    assert row["score"] == {"reference": 1, "found": {"submission": {"0.20": 1, "0.10": 0, "0.05": 0},
                                                      "sella": {"0.20": 0, "0.10": 0, "0.05": 0}}}



def test_explorer_overlay_carries_symmetry_labels_without_changing_scores(export):
    from saddlebench.explorer import score_overlay, symmetry
    from saddlebench.scoring.uma import UmaScoring

    scoring = UmaScoring(UmaDataset(export.parent / "cache"))
    assert symmetry(scoring) == {}  # the published file targets the real export's digest, not this fixture
    payload = _explorer_payload("parent", ["saddle"])
    matched = {("seed", "sella"): {"0.20": [], "0.10": [], "0.05": []}}
    families = {"seed": {"operations": [{"label": "identity", "seed_maxdev": 0.0}, {"label": "mirror ⟂ x at 1.50 Å", "seed_maxdev": 0.01}],
                         "families": [["mirror_partner", "saddle"]],
                         "links": {"saddle": [{"saddle": "mirror_partner", "operation": 1, "maxdev": 0.04}]},
                         "near_null": {"saddle": {"product": "p0", "maxdev": 0.21, "dE": 0.05}}}}
    plain = score_overlay(scoring, "parent", {"seed"}, matched, {"sella": "Sella"}, payload)
    labelled = score_overlay(scoring, "parent", {"seed"}, matched, {"sella": "Sella"}, payload, families)
    assert plain["found"] == labelled["found"] and set(plain["reference"]) == set(labelled["reference"])
    assert labelled["seeds"] == ["seed"]
    entry = labelled["reference"]["saddle"]
    assert entry["family"] == "mirror_partner" and entry["via"] == "mirror ⟂ x at 1.50 Å"
    assert entry["nearNull"]["maxdev"] == 0.21
    assert "family" not in plain["reference"]["saddle"]


def test_symmetry_builder_finds_a_mirror_and_relabels_atoms():
    import importlib.util
    import numpy as np
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("build_uma_symmetry", Path(__file__).resolve().parents[1] / "scripts" / "build_uma_symmetry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cell = np.diag([10.0, 12.0, 20.0])
    # mirror-symmetric seed about x = 5: two Pt at x = 4 and 6, one O on the plane
    seed = {"numbers": np.array([78, 78, 8]), "pbc": (True, True, False), "cell_angstrom": cell,
            "positions_angstrom": np.array([[4.0, 6.0, 10.0], [6.0, 6.0, 10.0], [5.0, 7.0, 11.0]])}
    ops = module.seed_symmetries(seed)
    labels = [module.describe(op) for op in ops]
    assert labels[0] == "identity" and any(label.startswith("mirror ⟂ x at 5.00") for label in labels)
    mirror = next(op for op in ops if module.describe(op).startswith("mirror ⟂ x"))
    saddle = {**seed, "positions_angstrom": seed["positions_angstrom"] + np.array([[0, 0, 0], [0, 0, 0], [0.4, 0, 0]])}
    image = module.transform(mirror, saddle)["positions_angstrom"]
    assert np.allclose(image[2], [4.6, 7.0, 11.0]) and np.allclose(sorted(image[:2, 0]), [4.0, 6.0])


def test_literature_notes_follow_the_family_code():
    from saddlebench.explorer import literature

    note = literature("lit_battery:B01")
    assert note["links"][0]["href"].startswith("https://doi.org/")
    assert note["mechanism"]
    reseeded = literature("lit_bulk:L01_V_Si215_3x3x3_v20261115")
    assert reseeded["mechanism"].startswith("Si vacancy")
    assert literature("oc20:al_385") is None


def test_manifest_rejects_tampered_table(export):
    path = Path(export) / "seeds.parquet"
    with path.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch for seeds.parquet"):
        UmaDataset().table("seeds")


def test_inclusive_maxdev_cutoff():
    reference = Atoms("H4", positions=[[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]])
    scorer = SeedScorer({"reference": geometry(reference)})
    for distance, matches in [(0.2, 1), (0.20000001, 0)]:
        prediction = reference.copy()
        prediction.positions[-1, 0] += distance
        result = scorer.score([geometry(prediction)])
        assert result["matched_reference_groups"]["0.20"] == matches


def test_benchmark_search_geometry_uses_attempt_key(tmp_path):
    from gzip import compress
    from saddlebench.reference.uma import BenchmarkSearchFrames

    atoms = Atoms("H", positions=[[0.125, 0, 0]])
    atoms.info["saddlebench_attempt_seq"] = 42
    native = tmp_path / "local.traj"
    write(native, atoms)
    shard = tmp_path / "search0001.traj.gz"
    shard.write_bytes(compress(native.read_bytes()))
    manifest = {"source_hf_revision": REVISION, "structures": {
        "parent": {"shard": shard.name, "frames": 1,
                   "sha256": hashlib.sha256(shard.read_bytes()).hexdigest()},
    }}
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))

    [(seq, prediction)] = BenchmarkSearchFrames(directory=tmp_path).frames("parent")
    assert seq == 42
    assert prediction.positions.tolist() == [[0.125, 0.0, 0.0]]


def test_all_atom_screen_checks_atoms_outside_the_default_sample():
    from saddlebench.matching.geometry import BatchedAnchorFilter

    reference = Atoms("H10", positions=[[i, 0, 0] for i in range(10)])
    screening = BatchedAnchorFilter([geometry(reference)], all_atoms=True)

    prediction = reference.copy()
    prediction.positions[4, 1] = 0.55
    assert not screening.possible(geometry(prediction), 0.2)[0]

    prediction.positions[4, 1] = 0.2
    assert screening.possible(geometry(prediction), 0.2)[0]
    assert SeedScorer({"ref": geometry(reference)}).score(
        [geometry(prediction)]
    )["matched_reference_groups"]["0.20"] == 1


def test_staged_screen_checks_atoms_outside_its_first_pass():
    from saddlebench.matching.geometry import BatchedAnchorFilter

    reference = Atoms("H20", positions=[[i, 0, 0] for i in range(20)])
    references = []
    for amount in range(20):
        configuration = reference.copy()
        configuration.positions[5:, 1] += amount * 0.01
        references.append(geometry(configuration))
    screening = BatchedAnchorFilter(references, all_atoms=True)
    _, _, _, sample = next(iter(screening.buckets.values()))
    assert len(sample) == 16
    original = {0, 5, 10, 15, 19}
    assert original.issubset(sample)
    prediction = reference.copy()
    prediction.positions[2, 1] = 0.55
    assert not screening.possible(geometry(prediction), 0.2).any()
    prediction.positions[2, 1] = 0.19
    assert screening.possible(geometry(prediction), 0.2)[0]

    # An inconclusive extra atom must preserve a rejection from the original five.
    extra = next(i for i in sample if i not in original)
    prediction = reference.copy()
    prediction.positions[5, 1] = 0.55
    prediction.positions[extra, 1] = 1e6
    assert not screening.possible(geometry(prediction), 0.2)[0]


def test_priority_search_preserves_nearest_reference_and_ties():
    from types import SimpleNamespace
    from saddlebench.matching.matcher import PreparedReferenceMatcher

    atoms = Atoms("H4", positions=[[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]])
    submission = {"geometry": geometry(atoms)}
    records = []
    for ident, distance in (("first", .18), ("tie-b", .04), ("tie-a", .04), ("last", .15)):
        candidate = atoms.copy()
        candidate.positions[-1, 0] = distance
        records.append({"reference_id": ident, "identity_id": ident, "geometry": geometry(candidate)})
    matcher = PreparedReferenceMatcher(SimpleNamespace(records=records, by_numbers=None))

    expected = matcher.match(submission)["matches"][0]
    matcher.prepared.clear()
    ranked = zip([0., .02, .03, .10], records)
    result = matcher.match(submission, ordered_bounds=ranked)["matches"][0]
    assert result == expected
    assert result["identity_id"] == "tie-a"
    assert id(records[-1]["geometry"]) not in matcher.prepared


def test_recall_only_retains_unmatched_frame_count(export):
    path = export.parent / "predictions.traj"
    output = export.parent / "recall.json"
    assert main(["score", "--case-id", "case0001", "--submission", str(path),
                 "--output", str(output), "--recall-only"]) == 0
    result = json.loads(output.read_text())
    assert result["recall"]["0.20"] == 1
    assert result["outside_reference_predictions"] == 2
    assert result["outside_reference_groups"] is None


@pytest.mark.parametrize("pbc", [[False, False, False], [True, True, False], [True, True, True]])
def test_profile_index_matches_exhaustive_under_drift_and_wrapping(pbc):
    import numpy as np

    cell = np.array([[20., 0., 0.], [3., 20., 0.], [1., 2., 20.]])
    atoms = Atoms("H8", positions=[[j*.75, 0., 0.] for j in range(8)], cell=cell, pbc=pbc)
    references = {}
    for i in range(24):
        ref = atoms.copy()
        ref.positions[-1, 1] += i * .17
        references[str(i)] = geometry(ref, arrays=True)
    scorer = SeedScorer(references)
    assert scorer.index is not None
    for movement in (.05, .19, .25, 14.):
        query = atoms.copy()
        query.positions += [3., -2.5, .5]
        if pbc[1]:
            query.positions[::2] += cell[1]
        query.positions[-1, 1] += movement
        prediction = geometry(query, arrays=True)
        exhaustive = scorer.matcher.match({"geometry": prediction})["matches"]
        assert scorer.nearest(prediction) == (exhaustive[0] if exhaustive else None)

    query.cell[0, 0] += .01
    prediction = geometry(query, arrays=True)
    assert scorer.index.candidates(prediction, .20) is None
    exhaustive = scorer.matcher.match({"geometry": prediction})["matches"]
    assert scorer.nearest(prediction) == (exhaustive[0] if exhaustive else None)


def test_decoded_reference_cache_reuses_verified_geometry(export, monkeypatch):
    from saddlebench.scoring.uma import UmaScoring

    directory = export.parent / "cache"
    original = UmaScoring(UmaDataset(directory)).structure("parent", ["seed"])["seed"]

    def unavailable(*args):
        raise AssertionError("Warm scoring should reuse the decoded geometry")

    monkeypatch.setattr(UmaDataset, "frames", unavailable)
    warm = UmaScoring(UmaDataset(directory)).structure("parent", ["seed"])["seed"]
    prediction = {"geometry": original.records[0]["geometry"]}
    assert original.nearest(prediction["geometry"]) == warm.nearest(prediction["geometry"])


def test_one_profile_candidate_still_requires_exact_match():
    atoms = Atoms("H8", positions=[[j*.75, 0., 0.] for j in range(8)])
    refs = {}
    for i in range(24):
        ref = atoms.copy()
        ref.positions[-1, 1] += i * .8
        refs[str(i)] = geometry(ref, arrays=True)
    scorer = SeedScorer(refs)
    for displacement, expected in [(0.19, "0"), (0.25, None)]:
        prediction = atoms.copy()
        prediction.positions[-1, 1] += displacement
        geom = geometry(prediction, arrays=True)
        assert len(scorer.index.candidates(geom, .2)[0]) == 1
        match = scorer.nearest(geom)
        assert (match and match["identity_id"]) == expected


def test_dataset_score_and_seed_download(export, capsys):
    import shutil
    from ase.io import read

    folder = export.parent
    shutil.copyfile(folder / "predictions.traj", folder / "case0001.traj")
    output = folder / "scores.csv"
    html = folder / "scores.html"
    assert main(["cases", "--dataset", "lit_oc20"]) == 0
    assert "case0001,lit_oc20,parent,1" in capsys.readouterr().out
    assert main(["cases", "--dataset", "all"]) == 0
    assert "case0001,lit_oc20,parent,1" in capsys.readouterr().out
    assert main(["score", "--dataset", "lit_oc20", "--submission-dir", str(folder),
                 "--output", str(output), "--recall-only", "--html", str(html)]) == 0
    assert "case0001,lit_oc20,parent,4,1" in output.read_text()
    assert html.read_text().count("1/1 (100.0%)") == 3
    assert main(["seed", "--dataset", "lit_oc20", "--output-dir", str(folder / "seeds")]) == 0
    assert read(folder / "seeds" / "case0001.traj").info["saddlebench_id"] == "seed"


def test_aligned_overlay_removes_reference_group(export, monkeypatch):
    import saddlebench.scoring.uma as scoring

    (export / "uma_aligned.csv").write_text("minimum_id,saddle_id\nseed,saddle\n")
    monkeypatch.setattr(scoring, "files", lambda _: export)
    model = scoring.UmaScoring(UmaDataset(export.parent / "cache"))
    assert list(model.references["seed"]) == []
    assert model.aligned_counts["seed"] == 1


def test_al_and_pt_are_in_lit_oc20():
    from saddlebench.scoring.uma import dataset_label
    assert dataset_label("oc20:al_385", "oc20") == "lit_oc20"
    assert dataset_label("oc20:pt_343", "oc20") == "lit_oc20"
    assert dataset_label("oc20:1234", "oc20") == "oc20"



@pytest.mark.parametrize("export", ["actual"], indirect=True)
def test_actual_tail_alone_admits_a_seed(export, capsys):
    assert main(["cases", "--dataset", "lit_oc20"]) == 0
    assert "case0001,lit_oc20,parent,1,actual" in capsys.readouterr().out
    output = export.parent / "actual-score.json"
    assert main(["score", "--case-id", "case0001", "--submission", str(export.parent / "predictions.traj"),
                 "--output", str(output), "--recall-only"]) == 0
    result = json.loads(output.read_text())
    assert result["confidence_sources"] == ["actual"]
    assert result["eligible_reference_groups"] == 1


def test_vendored_explorer_matches_its_record():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts" / "vendor_explorer.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

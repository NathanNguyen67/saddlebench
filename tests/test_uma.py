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
    confident = getattr(request, 'param', True)
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
    write(shard, [seed, saddle, other])
    write(tmp_path / "predictions.traj", [saddle, other, saddle, other])
    write(tmp_path / "predictions.extxyz", [saddle, other, saddle, other])
    locations = {"geometry_path": "geometries/structure0001.traj"}
    case = {"case_id": "case0001", "minimum_id": "seed", "parent_id": "parent"}
    (root / "cases.csv").write_text(
        "case_id,minimum_id,parent_id,confident,geometry_path,geometry_index\n"
        f"case0001,seed,parent,{confident},geometries/structure0001.traj,0\n"
    )
    tables = {
        "seeds": [{**case, **locations, "geometry_index": 0, "confident": confident}],
        "saddles": [{**locations, "parent_id": "parent", "saddle_id": sid, "geometry_index": i}
                    for i, sid in [(1, "saddle"), (2, "other")]],
        "reference": [{**case, "saddle_id": sid, "barrier_eV": barrier}
                      for sid, barrier in [("saddle", .99), ("other", 1.0)]],
        "structures": [{"parent_id": "parent", "dataset": "lit_metal_surfaces"}],
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
    assert main(["cases", "--dataset", "lit_oc20"]) == 0
    assert "case0001,lit_oc20,parent,1" in capsys.readouterr().out
    assert main(["cases", "--dataset", "all"]) == 0
    assert "case0001,lit_oc20,parent,1" in capsys.readouterr().out
    assert main(["score", "--dataset", "lit_oc20", "--submission-dir", str(folder),
                 "--output", str(output), "--recall-only"]) == 0
    assert "case0001,lit_oc20,parent,4,1" in output.read_text()
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

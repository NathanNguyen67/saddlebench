import hashlib
import json
from pathlib import Path

from ase import Atoms
from ase.io import write
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saddlebench.public_entrypoint import main
from saddlebench.reference.uma import REPO, REVISION, UmaDataset, select_endpoint_pair
from saddlebench.scoring.uma import SeedScorer, geometry


def pair(a, b, ea, eb, connected=None):
    return dict(minimum_a=a, minimum_b=b, energy_a_eV=ea, energy_b_eV=eb,
                seed_barriers=[{"minimum_id": connected}] if connected else [],
                n_complete=1, n_observations=2, parent_id="parent", saddle_id="saddle")


def test_endpoint_choice():
    fallback = pair("a", "b", -12., -13.)
    connected = pair("seed", "c", -8., -9., "seed")
    best = pair("seed", "b", -8., -10., "seed")
    tie = pair("seed", "d", -8., -10., "seed")
    assert select_endpoint_pair([fallback, connected, tie, best], "seed") is best
    assert select_endpoint_pair([best, connected, fallback], "other") is fallback
    assert select_endpoint_pair([tie, best], "seed") is best
    assert select_endpoint_pair([], "seed") is None


@pytest.fixture
def export(tmp_path, monkeypatch):
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
        "case0001,seed,parent,False,geometries/structure0001.traj,0\n"
    )
    tables = {
        "seeds": [{**case, **locations, "geometry_index": 0, "confident": False}],
        "saddles": [{**locations, "parent_id": "parent", "saddle_id": sid, "geometry_index": i}
                    for i, sid in [(1, "saddle"), (2, "other")]],
        "reference": [{**case, "saddle_id": sid, "barrier_eV": barrier}
                      for sid, barrier in [("saddle", .99), ("other", 1.0)]],
        "endpoint_pairs": [pair("a", "seed", -3, -4, "seed"),
                           pair("seed", "seed", -4, -4, "seed")],
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
    assert result["default_endpoint_pairs"]["saddle"]["n_observations"] == 2
    assert result["default_endpoint_pairs"]["saddle"]["minimum_a"] == "a"


def test_confidence_filter(export, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["score", "--case-id", "case0001", "--submission", "unused.traj", "--confident-only"])
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

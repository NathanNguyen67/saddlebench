"""Dataset selection and published starting minima for SaddleBench UMA."""
import argparse
import csv
from pathlib import Path
import sys

from ase.io import write

from saddlebench.reference.uma import UmaDataset
from saddlebench.scoring.uma import UmaScoring


def select_cases(scoring, dataset=None):
    return [seed for seed in sorted(scoring.seeds.values(), key=lambda seed: seed["case_id"])
            if seed["confident"] and (
                dataset is None or scoring.datasets[seed["parent_id"]] == dataset
                or dataset == "literature" and scoring.datasets[seed["parent_id"]].startswith("lit_")
            )]


def add_dataset_argument(parser, *, required=False, default=None):
    parser.add_argument("--dataset", required=required, default=default, help=(
        "Benchmark dataset, e.g. lit_oc20, lit_oc22, lit_bulk, lit_battery, oc20, oc22, "
        "lemat, mp20bat, or literature for all lit_* datasets"
    ))


def check_dataset(parser, scoring, label):
    if label is not None and label not in set(scoring.datasets.values()) | {"literature"}:
        parser.error(f"unknown dataset {label!r}; run saddlebench cases to see dataset names")


def main_cases(argv):
    parser = argparse.ArgumentParser(prog="saddlebench cases")
    add_dataset_argument(parser)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    scoring = UmaScoring(UmaDataset(args.cache_dir))
    check_dataset(parser, scoring, args.dataset)
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(("case_id", "dataset", "parent_id", "eligible_reference_groups"))
    for seed in select_cases(scoring, args.dataset):
        writer.writerow((seed["case_id"], scoring.datasets[seed["parent_id"]], seed["parent_id"],
                         len(scoring.references[seed["minimum_id"]])))
    return 0


def main_seed(argv):
    parser = argparse.ArgumentParser(prog="saddlebench seed")
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument("--case-id")
    add_dataset_argument(select)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path, help="Single ASE .traj for --case-id")
    output.add_argument("--output-dir", type=Path, help="One ASE .traj per confident case for --dataset")
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    if bool(args.case_id) != bool(args.output):
        parser.error("use --case-id with --output or --dataset with --output-dir")
    dataset = UmaDataset(args.cache_dir)
    if args.case_id:
        if args.case_id not in dataset.cases:
            parser.error(f"unknown UMA case {args.case_id!r}")
        records = [dataset.cases[args.case_id]]
        # CSV frame pointers are strings.
        records[0] = {**records[0], "geometry_index": int(records[0]["geometry_index"])}
    else:
        scoring = UmaScoring(dataset)
        check_dataset(parser, scoring, args.dataset)
        records = select_cases(scoring, args.dataset)
        args.output_dir.mkdir(parents=True, exist_ok=True)
    by_id = {record["minimum_id"]: record for record in records}
    for ident, atoms in dataset.frames(by_id):
        path = args.output or args.output_dir / f"{by_id[ident]['case_id']}.traj"
        if path.exists():
            raise FileExistsError(path)
        write(path, atoms, format="traj")
    print(f"Wrote {len(by_id)} seed geometries")
    return 0

"""Benchmark metrics shared by the UMA CSV and HTML reports."""
from math import exp, fsum, isfinite

from ase.units import kB

from saddlebench.scoring.uma import THRESHOLDS

TEMPERATURE_K = 423
METHOD_LABELS = {
    "sella_prfo_diag4_abs01": "Sella PRFO m24 0.1 Torque",
    "tsbfgs_dimer_rot4_frot01_r23_allprobes": "TSBFGS dimer",
    "sella_motif_augmented": "Sella motif augmented",
    "tsbfgs_dimer_motif_augmented": "TSBFGS dimer motif augmented",
}
AUGMENTED = {
    "sella_prfo_diag4_abs01": "sella_motif_augmented",
    "tsbfgs_dimer_rot4_frot01_r23_allprobes": "tsbfgs_dimer_motif_augmented",
}


def rate_weights(reference):
    weights = {}
    for ident, record in reference.items():
        barrier = record["barrier_eV"]
        if not isfinite(barrier):
            raise ValueError(f"Non-finite reference barrier for {ident}")
        weights[ident] = exp(-barrier / (kB * TEMPERATURE_K))
    return weights


def rate_totals(reference, matched):
    weights = rate_weights(reference)
    result = {"eligible_rate_weight": fsum(weights.values())}
    for threshold in THRESHOLDS:
        key = f"{threshold:.2f}"
        result[f"found_rate_weight_{key}_A"] = fsum(weights[ident] for ident in matched[key])
    return result


def first_hit_events(attempts, evaluated, matches, reference, *, proposal_time=None):
    """Record novel hits on a serial-equivalent clock using reported attempt durations."""
    if any(row["total_wall_s"] is None for row in attempts):
        return None
    if len(matches) > len(evaluated) and proposal_time is None:
        return None
    clock = 0.0
    durations = {}
    for row in attempts:
        clock += row["total_wall_s"]
        durations[row["seq"]] = clock
    times = [durations[row["seq"]] for row in evaluated]
    if proposal_time is not None:
        clock += proposal_time
    if len(matches) > len(evaluated):
        times.extend([clock] * (len(matches) - len(evaluated)))
    weights = rate_weights(reference)
    seen = [set() for _ in THRESHOLDS]
    events = []
    for time, match in zip(times, matches):
        if match is None:
            continue
        sid, distance = match["identity_id"], match["maxdev_angstrom"]
        for index, threshold in enumerate(THRESHOLDS):
            if distance <= threshold and sid not in seen[index]:
                seen[index].add(sid)
                events.append((time, index, weights[sid]))
    return clock, events

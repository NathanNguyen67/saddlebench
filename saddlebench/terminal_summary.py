"""Human-readable, read-only presentation of published SaddleBench quick reports.

The scorer's JSON schema and scientific calculations are unchanged. A displayed
first-hit known-bad match is NOT a canonical verified bad-reaction count.
"""
from collections import Counter
import csv
import math
from pathlib import Path


def _case_metadata(cases_csv):
    """Read historical reference exploration metadata, not new search results."""
    if cases_csv is None or not Path(cases_csv).is_file():
        return {}
    with Path(cases_csv).open(newline="", encoding="utf-8") as stream:
        return {row["case_id"]: row for row in csv.DictReader(stream)}


def _maybe_float(value):
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _number(value, digits=2):
    return "N/A" if value is None else f"{value:,.{digits}f}"


def _bar(found, known, width=24):
    filled = max(0, min(width, round(width * found / known))) if known else 0
    return "[" + "#" * filled + "." * (width - filled) + "]"


def render_summary(report, *, cases_csv=None):
    """Format a single-case or multi-case quick report without modifying it."""
    metadata = _case_metadata(cases_csv)
    mode = report.get("mode", "unknown")
    cases = report.get("case_results")
    if cases is None:
        cases = [report]
    lines = ["", "=" * 67,
             f" SADDLEBENCH    MODE: {mode}    CASES: {len(cases)}",
             "=" * 67]
    aggregate = report.get("aggregate")
    if aggregate is not None:
        k = aggregate.get("reference_known_K")
        u = aggregate.get("greedy_unique_reactions_U")
        coverage = aggregate.get("micro_coverage")
        summary = (f"ALL CASES | Reactions: {u if u is not None else 'N/A'}/"
                   f"{k if k is not None else 'N/A'}"
                   f" | Coverage: {_number(coverage * 100 if coverage is not None else None)}%")
        if mode != "coverage":
            summary += f" | Provisional score/hour: {_number(aggregate.get('provisional_score_per_hour'), 4)}"
        lines.append(summary)

    for case in cases:
        entries = case.get("entries")
        if entries is None:
            # Older/third-party reports may not contain prediction-level details.
            entries = []
            entries_available = False
        else:
            entries_available = True
        stages = Counter(e.get("stage") or "unmatched" for e in entries)
        statuses = Counter(e.get("status") for e in entries)
        categories = Counter(e.get("category") for e in entries if e.get("stage") not in (None, "eligible"))
        eligible = stages["eligible"]
        other = sum(n for stage, n in stages.items() if stage not in ("eligible", "unmatched"))
        unmatched = stages["unmatched"]
        k = case.get("known_eligible_reactions_K")
        u = case.get("greedy_unique_reactions_U")
        ktext = str(k) if k is not None else "N/A"
        utext = str(u) if u is not None else "N/A"
        seconds = case.get("self_reported_elapsed_seconds")
        case_id = case.get("case_id", "UNKNOWN_CASE")
        lines.extend(["",
                      f"{case_id} | {len(entries) if entries_available else 'N/A'} predictions | {_number(seconds, 0)} sec",
                      (f"  Reaction coverage: {utext}/{ktext} {_bar(u or 0, k or 0)} "
                       f"{_number(case.get('coverage') * 100 if case.get('coverage') is not None else None)}%"),
                      f"  {'Coverage-only score preview' if mode == 'coverage' else 'Provisional score'}: {_number(case.get('provisional_score_per_hour'), 4)} / hour"])
        # This number is computed from *historical reference observations* when
        # building public cases.csv; it is not a confidence interval for U/K,
        # nor does it mean all physical saddles have been discovered.
        meta = metadata.get(case_id)
        if meta is not None:
            # Reject incorrectly paired metadata rather than showing a
            # confidence for a different starting minimum.
            if (case.get('structure_id') not in (None, meta.get('structure_id'))
                or case.get('starting_minimum_id') not in (None, meta.get('starting_minimum_id'))):
                lines.append("  Reference exploration confidence: N/A (case identity mismatch)")
            else:
                confidence = _maybe_float(meta.get('exploration_confidence_heuristic'))
                tail = _maybe_float(meta.get('n_redundant_tail_mean'))
                if confidence is not None and 0 <= confidence <= 1:
                    lines.append(f"  Reference exploration confidence (heuristic): {confidence * 100:.2f}%")
                else:
                    lines.append("  Reference exploration confidence (heuristic): N/A (insufficient historical repeat evidence)")
                if tail is not None:
                    lines.append(f"  Historical redundant tail (shuffled mean): {tail:.2f} observations")
        elif cases_csv is not None:
            lines.append("  Reference exploration confidence: N/A (case not found in cases.csv)")
        if not entries_available:
            lines.append("  Prediction classification: not available in this report")
            continue
        lines.extend([
            f"  Eligible reaction matches:    {eligible} predictions (includes duplicates)",
            f"  Distinct eligible reactions:  {utext}",
            f"  Duplicate eligible matches:  {statuses['DUPLICATE']} predictions",
        ])
        if mode == "coverage":
            lines.append(f"  No eligible reaction match:   {unmatched} (other saddles not checked)")
            continue

        nonfo = categories["KNOWN_NON_FIRST_ORDER"]
        high_barrier = categories["KNOWN_CONNECTED_HIGH_BARRIER"]
        potential_bad = nonfo + high_barrier
        lines.extend([
            f"  Other known saddle matches:   {other} predictions",
            f"  No match in checked stages:   {unmatched} predictions",
            f"  Any known reference match:    {eligible + other}/{len(entries)} predictions",
            f"  Potential bad first-hit matches: {potential_bad} predictions",
            f"    Known non-first-order:     {nonfo} predictions",
        ])
        if mode == "score_detailed":
            lines.append(f"    Known connected high barrier: {high_barrier} predictions")
            other_categories = [(name, n) for name, n in categories.items()
                                if name not in ("KNOWN_NON_FIRST_ORDER", "KNOWN_CONNECTED_HIGH_BARRIER")]
            if other_categories:
                lines.append("  Other diagnostic categories (first hit):")
                for name, count in sorted(other_categories, key=lambda x: (-x[1], str(x[0]))):
                    lines.append(f"    {name or 'UNKNOWN'}: {count} predictions")
        else:
            lines.append("  Remaining diagnostics: not checked (use --mode score_detailed)")
        b = case.get("first_hit_potential_bad_groups_with_known_start")
        lines.append(f"  Potential bad groups, verified start (first-hit): {b if b is not None else 'N/A'}")

    lines.extend(["", "NOTE: Reference exploration confidence is a historical rediscovery heuristic,",
                  "not the probability that all saddles are known or that this run is complete.",
                  "Unknown predictions are not automatically bad or novel.",
                  "Potential bad first-hit matches/groups are NOT canonical bad-reaction counts.",
                  "Coverage mode does not check other saddle types; scores differ by mode."])
    return "\n".join(lines)

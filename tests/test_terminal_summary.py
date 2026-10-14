"""Formatting-only tests; no calculator, network or reference data needed."""
import copy
from saddlebench.terminal_summary import render_summary


def case(mode="score_detailed", name="case0006"):
    es = [
        {"stage": "eligible", "status": "CREDIT", "category": "CONNECTED_RATE_RELEVANT_REACTION"},
        {"stage": "eligible", "status": "DUPLICATE", "category": "CONNECTED_RATE_RELEVANT_REACTION"},
        {"stage": "known_nonfo", "status": "NONFO_FIRST_HIT", "category": "KNOWN_NON_FIRST_ORDER"},
        {"stage": "remaining_diagnostics", "status": "CATALOG_FIRST_HIT", "category": "KNOWN_CONNECTED_HIGH_BARRIER"},
        {"stage": "remaining_diagnostics", "status": "CATALOG_FIRST_HIT", "category": "KNOWN_FIRST_ORDER_DISCONNECTED"},
        {"stage": None, "status": "UNKNOWN_UNMATCHED", "category": None},
    ]
    return {"case_id": name, "mode": mode, "entries": es, "known_eligible_reactions_K": 4,
            "greedy_unique_reactions_U": 1, "coverage": .25,
            "self_reported_elapsed_seconds": 532., "provisional_score_per_hour": 1.2345,
            "first_hit_potential_bad_groups_with_known_start": 2,
            "canonical_known_bad_unique_B": None}


def test_single_detailed_prints_bad_and_neutral_separately():
    report = case()
    frozen = copy.deepcopy(report)
    output = render_summary(report)
    assert "case0006 | 6 predictions" in output
    assert "Reaction coverage: 1/4" in output
    assert "25.00%" in output
    assert "Known non-first-order:     1 predictions" in output
    assert "Known connected high barrier: 1 predictions" in output
    assert "Potential bad first-hit matches: 2 predictions" in output
    assert "KNOWN_FIRST_ORDER_DISCONNECTED: 1 predictions" in output
    assert "No match in checked stages:   1 predictions" in output
    assert "Potential bad groups, verified start (first-hit): 2" in output
    assert "NOT canonical bad-reaction counts" in output
    assert report == frozen


def test_coverage_does_not_label_unmatched_as_bad():
    report = case("coverage")
    report["entries"] = report["entries"][:2] + [
        {"stage": None, "status": "UNKNOWN_UNMATCHED", "category": None}
    ] * 4
    output = render_summary(report)
    assert "No eligible reaction match:   4 (other saddles not checked)" in output
    assert "Potential bad first-hit matches:" not in output
    assert "Coverage-only score preview" in output


def test_score_mode_does_not_claim_high_barrier_checked():
    report = case("score")
    report["entries"] = report["entries"][:3]
    output = render_summary(report)
    assert "Potential bad first-hit matches: 1 predictions" in output
    assert "Remaining diagnostics: not checked" in output
    assert "Known connected high barrier" not in output


def test_batch_and_missing_fields_and_k_zero():
    a=case(); b=case(name="case0022");b["known_eligible_reactions_K"]=0
    b["greedy_unique_reactions_U"]=0;b["coverage"]=None
    result={"mode":"score_detailed", "case_results":[a,b], "aggregate":{
        "greedy_unique_reactions_U":1,"reference_known_K":4,"micro_coverage":.25,
        "provisional_score_per_hour": 0.1}}
    out=render_summary(result)
    assert "CASES: 2" in out and "case0022" in out
    assert "ALL CASES | Reactions: 1/4" in out
    assert "0/0" in out and "N/A%" in out
    assert "Prediction classification: not available" in render_summary({"mode":"coverage","case_id":"test"})


def test_reference_confidence_two_cases_from_published_csv(tmp_path):
    csvpath = tmp_path / 'cases.csv'
    csvpath.write_text(
        'case_id,structure_id,starting_minimum_id,exploration_confidence_heuristic,n_redundant_tail_mean,confidence_basis\n'
        'case0006,oc20:a,minimum:1,0.95,200,SHUFFLED_ELIGIBLE_OBSERVATIONS_PROXY\n'
        'case0022,oc20:b,minimum:2,0.75,40,SHUFFLED_ELIGIBLE_OBSERVATIONS_PROXY\n',
        encoding='utf-8')
    a = case(name='case0006')
    a.update(structure_id='oc20:a', starting_minimum_id='minimum:1')
    b = case(name='case0022')
    b.update(structure_id='oc20:b', starting_minimum_id='minimum:2')
    batch = {'mode': 'score_detailed', 'case_results': [a, b]}
    snapshot = copy.deepcopy(batch)
    out = render_summary(batch, cases_csv=csvpath)
    assert 'case0006' in out and '95.00%' in out
    assert 'case0022' in out and '75.00%' in out
    assert 'Historical redundant tail (shuffled mean): 200.00 observations' in out
    assert 'not the probability that all saddles are known' in out
    assert batch == snapshot


def test_missing_and_mismatched_reference_confidence(tmp_path):
    csvpath = tmp_path / 'cases.csv'
    csvpath.write_text('case_id,structure_id,starting_minimum_id,exploration_confidence_heuristic,n_redundant_tail_mean\n'
                       'case0006,expected,minimum:1,,\n', encoding='utf-8')
    match = case(name='case0006')
    match.update(structure_id='expected', starting_minimum_id='minimum:1')
    assert 'N/A (insufficient historical repeat evidence)' in render_summary(match, cases_csv=csvpath)
    mismatch = case(name='case0006')
    mismatch.update(structure_id='wrong', starting_minimum_id='minimum:1')
    assert 'N/A (case identity mismatch)' in render_summary(mismatch, cases_csv=csvpath)
    unknown = case(name='case0099')
    assert 'N/A (case not found in cases.csv)' in render_summary(unknown, cases_csv=csvpath)
    # Existing API continues to work without an optional metadata file.
    assert 'Reference exploration confidence:' not in render_summary(match)

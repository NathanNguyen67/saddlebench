import copy
import pytest
from saddlebench.scoring.engine import classify,score
from examples.generate_fixture import geom

def sub(y,name='s1',start='min-A',pes='PES-A',parent='parent-A'):
    return {'geometry':geom(y),'submission_id':name,'starting_minimum_id':start,'pes_id':pes,'parent_structure_id':parent}

def test_primary_and_dedup(refs):
    report=score([sub(0),sub(0,'duplicate')],refs,3600.)
    assert report['eligible_unique_count']==1
    assert report['primary_score_per_wall_hour']==1.
    assert report['coverage']['denominator']==3
    assert report['coverage']['numerator']==1
    assert report['coverage']['value']==pytest.approx(1/3)
    assert 'DUPLICATE_REFERENCE_IDENTITY' in report['per_submission'][1]['reason_codes']

def test_independent_science_states(refs):
    report=score([sub(y,str(y)) for y in (2,4,6,8,10,12,14,16)],refs,3600)
    r=report['per_submission']
    assert r[0]['barrier_state']=='AT_OR_ABOVE_1P5' and r[0]['fo_state']=='CERTIFIED_FO'
    assert r[1]['barrier_state']=='AT_OR_ABOVE_1P5'
    assert r[2]['fo_state']=='CERTIFIED_NON_FO' and r[2]['barrier_state']=='BELOW_1P5'
    assert r[3]['fo_state']=='UNKNOWN'
    assert r[4]['connection_state']=='UNKNOWN'
    assert r[5]['provenance_state']=='NONCANONICAL'
    assert r[6]['barrier_state']=='NONCOMPARABLE_PES'
    assert r[7]['connection_state']=='DISCONNECTED_FROM_START'
    assert report['eligible_unique_count']==0

def test_unmatched_and_ambiguous(refs):
    r=score([sub(35),sub(18.15,'ambiguous')],refs,3600)['per_submission']
    assert r[0]['geometry_state']=='NOT_IN_REFERENCE' and r[0]['fo_state']=='UNKNOWN'
    assert r[1]['geometry_state']=='AMBIGUOUS_REFERENCE_MATCH' and r[1]['connection_state']=='UNKNOWN'

def test_start_pes_unknown_no_credit(refs):
    r=score([sub(0,start='incorrect'),sub(0,'badpes',pes='wrong'),sub(0,'no-start',start=None)],refs,3600)
    assert r['eligible_unique_count']==0
    assert r['coverage']['reason']=='MIXED_SCOPES'
    assert r['per_submission'][1]['barrier_state']=='NONCOMPARABLE_PES'

def test_finite_positive_time(refs):
    for elapsed in [0.,-1.,float('nan'),float('inf'),float('-inf')]:
        with pytest.raises(ValueError):score([sub(0)],refs,elapsed)

def test_empty_is_zero_not_fabricated_coverage(refs):
    r=score([],refs,3600)
    assert r['primary_score_per_wall_hour']==0.
    assert r['coverage']['value'] is None and r['submission_count']==0

def test_counterfeit_unverified_certification(refs):
    a=copy.deepcopy(next(r for r in refs.records if r['reference_id']=='a'))
    a['hessian']['verified']=False
    assert classify(a,'min-A','PES-A','parent-A')['fo_state']=='UNKNOWN'
    a['doublemin']['source_sha256']='fake'
    assert classify(a,'min-A','PES-A','parent-A')['connection_state']=='UNKNOWN'

def test_submissions_unique_ids(refs):
    with pytest.raises(ValueError,match='DUPLICATE_SUBMISSION_ID'):score([sub(0),sub(2)],refs,3600)

def test_missing_event_does_not_count(refs):
    a=copy.deepcopy(next(r for r in refs.records if r['reference_id']=='a'))
    a['evidence_sources'].pop('producer_event_id')
    states=classify(a,'min-A','PES-A','parent-A')
    assert states['fo_state']=='CERTIFIED_FO'
    assert states['eligible'] is False
    assert 'UNPUBLISHED_OR_UNVERIFIED_PRODUCER_EVENT' in states['reason_codes']

def test_unpublished_event_does_not_count(refs):
    a=copy.deepcopy(next(r for r in refs.records if r['reference_id']=='a'))
    a['evidence_sources']['event_sequence']=9
    assert not classify(a,'min-A','PES-A','parent-A',0,None)['eligible']

def test_negative_barrier_anomaly_not_auto_promoted(refs):
    a=copy.deepcopy(next(r for r in refs.records if r['reference_id']=='a'))
    a['energy']['saddle_ev']=-10.1
    states=classify(a,'min-A','PES-A','parent-A')
    assert states['barrier_state']=='BELOW_1P5'
    assert 'NEGATIVE_BARRIER_ANOMALY_REQUIRES_REVIEW' in states['reason_codes']
    assert states['eligible'] is False

def test_unresolved_reference_reason_blocks_positive(refs):
    a=copy.deepcopy(next(r for r in refs.records if r['reference_id']=='a'))
    a['uncertainty_reasons']=['INCOMPLETE_SOURCE_MAPPING']
    assert classify(a,'min-A','PES-A','parent-A')['eligible'] is False

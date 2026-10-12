"""Contract tests: both modes are reaction-based, never saddle-only."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pytest
from saddlebench.prototype.reaction_mvp import build,score

FIXTURE=Path(__file__).parent/'fixtures'/'synthetic_evidence_v1'

def dataset(tmp_path, *, protocol='legacy_historical_lbfgs_0p0025',
            saddle_fmax=.004, minus=.002, plus=.002,
            minus_status='converged', plus_status='converged', connected=1, rr=1):
    d=tmp_path/'dataset'; d.mkdir()
    m=json.loads((FIXTURE/'manifest.json').read_text())
    for name in m['file_sha256']:
        records=[json.loads(line) for line in (FIXTURE/name).read_text().splitlines()]
        if name=='saddles.jsonl':
            for s in records:
                s['source_columns']={'saddle_fmax_eV_A':saddle_fmax}
                s['observed_starting_minimum_ids']=['m0']
        if name=='doublemin_observations.jsonl':
            for o in records:
                o['protocol']=protocol
                o['protocol_eligible_registry_flag']=1
                o['minus_endpoint_evidence']={'stored_real_atomic_fmax_eV_A':minus,'terminal_status':minus_status}
                o['plus_endpoint_evidence']={'stored_real_atomic_fmax_eV_A':plus,'terminal_status':plus_status}
        if name=='reactions.jsonl':
            for r in records:
                r['connected_to_canonical_minimum']=connected
                r['rate_relevant_registry_flag']=rr
        raw=''.join(json.dumps(o,sort_keys=True,separators=(',',':'))+'\n' for o in records).encode()
        (d/name).write_bytes(raw)
        m['file_sha256'][name]=hashlib.sha256(raw).hexdigest()
    (d/'manifest.json').write_text(json.dumps(m))
    return d

def test_historical_allowed_and_canonical_rejected(tmp_path):
    d=dataset(tmp_path)
    counts=build(d,tmp_path/'all','all_saddles','pes:test')
    assert counts['eligible_reaction_identities']>=1
    reference=list((tmp_path/'all'/'reference.jsonl').read_text().splitlines())
    r=json.loads(reference[0])
    assert 'legacy' in r['supporting_protocols'][0]
    canon=build(d,tmp_path/'canonical','canonical_only','pes:test')
    assert canon['eligible_reaction_identities']==0

def test_mode_switch_powell_only(tmp_path):
    d=dataset(tmp_path,protocol='standalone_hessian_lbfgs_powell_eta0p2_fmax0p0025_disp0p25_v2')
    assert build(d,tmp_path/'canonical','canonical_only','pes:test')['eligible_reaction_identities']>=1

def test_non_powell_with_protocol_eligible_is_not_canonical(tmp_path):
    d=dataset(tmp_path,protocol='standalone_hessian_lbfgs_fmax0p0025_disp0p25_v1')
    assert build(d,tmp_path/'canonical','canonical_only','pes:test')['eligible_reaction_identities']==0
    assert build(d,tmp_path/'all','all_saddles','pes:test')['eligible_reaction_identities']>=1

@pytest.mark.parametrize('kwargs',[{'saddle_fmax':.00501},{'minus':.00251},{'plus':None},
                                  {'minus_status':'failed'},{'connected':0},{'rr':0}])
def test_fail_closed_on_required_reaction_evidence(tmp_path,kwargs):
    d=dataset(tmp_path,**kwargs)
    assert build(d,tmp_path/'all','all_saddles','pes:test')['eligible_reaction_identities']==0

def test_boundaries_inclusive(tmp_path):
    d=dataset(tmp_path,saddle_fmax=.005,minus=.0025,plus=.0025)
    assert build(d,tmp_path/'all','all_saddles','pes:test')['eligible_reaction_identities']>=1

def test_tampered_input_fail_closed(tmp_path):
    d=dataset(tmp_path)
    with (d/'saddles.jsonl').open('a') as f: f.write('{}\n')
    with pytest.raises(ValueError,match='CHECKSUM'):
        build(d,tmp_path/'all','all_saddles','pes:test')

def test_score_real_identity_and_duplicate(tmp_path):
    d=dataset(tmp_path)
    build(d,tmp_path/'ref','all_saddles','pes:test')
    ref=json.loads((tmp_path/'ref'/'reference.jsonl').read_text().splitlines()[0])
    g=ref['members'][0]['geometry']
    from saddlebench.io.extxyz import SYMBOLS
    text=''
    for index in range(2):
        header=f'Properties=species:S:1:pos:R:3 submission_id=p{index} starting_minimum_id=m0 parent_structure_id=synthetic:p1 pes_id=pes:test constraints_known=T fixed_indices="" Lattice="'+' '.join(str(v) for row in g['cell_angstrom'] for v in row)+'" pbc="F F F"'
        frame=[str(len(g['numbers'])),header]
        for n,pos in zip(g['numbers'],g['positions_angstrom']):frame.append(SYMBOLS[n-1]+' '+' '.join(map(str,pos)))
        text+='\n'.join(frame)+'\n'
    path=tmp_path/'sub.extxyz';path.write_text(text)
    result=score(tmp_path/'ref',path,3600.)
    assert result['eligible_unique_count']==1
    assert [r['status'] for r in result['submissions']]==['CREDIT','DUPLICATE']


def test_record_missing_optimizer_status_requires_measured_fmax(tmp_path):
    d=dataset(tmp_path,minus_status=None)
    counts=build(d,tmp_path/'all','all_saddles','pes:test')
    assert counts['eligible_reaction_identities']>=1
    assert counts['accepted_force_converged_but_optimizer_status_unknown']>=1


def test_lustre_einval_real_prototype_build_and_load(tmp_path, monkeypatch):
    """Exercise the full reference writer/reader, not just the low-level fallback."""
    import errno
    from saddlebench.reference import safe_output
    from saddlebench.prototype.reaction_mvp import load_reference
    def reject(*_args):
        raise OSError(errno.EINVAL, 'Lustre RENAME_NOREPLACE unsupported')
    monkeypatch.setattr(safe_output, '_rename_noreplace', reject)
    source = dataset(tmp_path)
    ref = tmp_path / 'lustre_ref'
    result = build(source, ref, 'all_saddles', 'pes:test')
    assert result['eligible_reaction_identities'] >= 1
    manifest, records = load_reference(ref)
    assert manifest['reference_count'] == len(records)
    assert (ref/'manifest.json').is_file()

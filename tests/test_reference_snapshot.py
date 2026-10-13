import copy,json
import pytest
from saddlebench.reference.snapshot import load_reference,export_snapshot
from examples.generate_fixture import frames

def test_integrity(refs):
    assert len(refs.records)==12
    path=refs.path/'records.jsonl'
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='CHECKSUM'):load_reference(refs.path)

def test_tamper_metadata(refs):
    m=refs.path/'manifest.json';d=json.loads(m.read_text());d['schema_version']='bad';m.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='UNSUPPORTED_REFERENCE_SCHEMA'):load_reference(refs.path)

def test_no_overwrite(refs):
    rows,_=frames()
    with pytest.raises(FileExistsError):export_snapshot(rows,refs.path,refs.manifest)

def test_no_guess_fields(refs,tmp_path):
    rows,_=frames();del rows[0]['hessian']
    with pytest.raises(ValueError,match='REFERENCE_FIELDS_MISSING'):export_snapshot(rows,tmp_path/'bad',refs.manifest)

def test_deterministic_export(refs,tmp_path):
    rows,_=frames();more=tmp_path/'other'
    export_snapshot(list(reversed(rows)),more,refs.manifest)
    assert (refs.path/'records.jsonl').read_bytes()==(more/'records.jsonl').read_bytes()
    assert (refs.path/'manifest.json').read_bytes()==(more/'manifest.json').read_bytes()

def test_constraint_unknown_does_not_block_geometry_match_or_credit(refs):
    from dataclasses import replace
    from saddlebench.matching.matcher import match
    from saddlebench.scoring.engine import score, classify
    from examples.generate_fixture import geom
    rows=list(copy.deepcopy(refs.records))
    for rec in rows:
        rec['geometry']['constraints_known']=False
        rec['geometry']['fixed_indices']=[]
    by_numbers={}
    for rec in rows:
        by_numbers.setdefault(tuple(rec['geometry']['numbers']),[]).append(rec)
    changed=replace(refs,records=tuple(rows),by_numbers={k:tuple(v) for k,v in by_numbers.items()})
    sub={'geometry':geom(0),'submission_id':'no-fixatoms',
         'starting_minimum_id':'min-A','pes_id':'PES-A','parent_structure_id':'parent-A'}
    assert match(sub,changed)['state']=='UNIQUE_REFERENCE_MATCH'
    r=score([sub],changed,3600)
    assert r['eligible_unique_count']==1
    candidate=next(x for x in rows if x['reference_id']=='a')
    assert classify(candidate,'min-A','PES-A','parent-A')['eligible'] is True

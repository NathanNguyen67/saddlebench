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

def test_constraint_unknown_is_not_eligible(refs):
    rec=copy.deepcopy(refs.records[0]);rec['geometry']['constraints_known']=False
    from saddlebench.matching.matcher import match
    assert match({'geometry':rec['geometry']},refs)['state']=='NOT_IN_REFERENCE'

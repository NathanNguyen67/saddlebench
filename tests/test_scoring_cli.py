import json
import pytest
from saddlebench.cli import main
from saddlebench.io.extxyz import read_extxyz
from examples.generate_fixture import write_example

def test_fixture_end_to_end(tmp_path):
    write_example(tmp_path)
    score=json.loads((tmp_path/'expected_score.json').read_text())
    assert score['eligible_unique_count']==1
    assert score['primary_score_per_wall_hour']==1
    assert score['submission_count']==13
    assert score['coverage']['denominator'] is None
    assert score['coverage']['reason']=='MIXED_SCOPES'
    assert (tmp_path/'expected_score.csv').read_text().count('\n')==14
    assert len(read_extxyz(tmp_path/'submissions.extxyz'))==13

def test_deterministic_repeat(tmp_path):
    write_example(tmp_path)
    original=(tmp_path/'expected_score.json').read_bytes()
    main(['score',str(tmp_path/'submissions.extxyz'),'--reference',str(tmp_path/'reference_v1'),
          '--elapsed-seconds','3600','--out-prefix',str(tmp_path/'expected_score')])
    assert (tmp_path/'expected_score.json').read_bytes()==original

def test_conflicting_scope(tmp_path):
    write_example(tmp_path)
    with pytest.raises(ValueError,match='CONFLICTING_CLI_AND_FRAME_SCOPE'):
        main(['score',str(tmp_path/'submissions.extxyz'),'--reference',str(tmp_path/'reference_v1'),
              '--elapsed-seconds','3600','--start-minimum-id','some-other-minimum'])

def test_reject_unsupported_properties(tmp_path):
    src=tmp_path/'bad.extxyz'
    src.write_text('1\nProperties=species:S:1:pos:R:3\nH 0 0\n')
    with pytest.raises(ValueError,match='COLUMNS'):read_extxyz(src)

def test_expected_manifest_pin_rejects_tampering(tmp_path):
    write_example(tmp_path)
    with pytest.raises(ValueError,match='EXPECTED_REFERENCE_MANIFEST_SHA_MISMATCH'):
        main(['score',str(tmp_path/'submissions.extxyz'),'--reference',str(tmp_path/'reference_v1'),
              '--elapsed-seconds','3600','--expected-manifest-sha256','0'*64])

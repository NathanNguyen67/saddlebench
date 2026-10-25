"""Regression gates for start-optional scorer and immutable derived annotations."""
import csv, hashlib, json
from pathlib import Path
import pytest
from saddlebench.matching.greedy_modes import _group_eligible
from saddlebench.scoring.scope import matches,scoped_ids
from saddlebench.score_modes import verify_reference
from tools.backfill_associated_starts import build


def record(reaction_id,start=None,structure='A',pes='PES-1'):
    return {'reaction_id':reaction_id,'structure_id':structure,'pes_id':pes,
            'starting_minimum_id':start,'members':[{'geometry':{'numbers':[1]}}]}


def test_start_off_by_default_and_on_explicitly():
    case={'structure_id':'A','pes_id':'PES-1','starting_minimum_id':'m1'}
    rx=[record('r1',None),record('r2','m2'),record('r3','m1'),record('r4','m1','B')]
    assert scoped_ids(rx,case)=={'r1','r2','r3'}
    assert scoped_ids(rx,case,require_starting_minimum=True)=={'r3'}
    assert len(_group_eligible(rx,case))==1
    assert len(_group_eligible(rx,case,True))==1
    assert sum(len(v) for v in _group_eligible(rx,case).values())==3
    assert sum(len(v) for v in _group_eligible(rx,case,True).values())==1
    assert matches(rx[0],case) and not matches(rx[0],case,require_starting_minimum=True)


def test_strict_start_requires_case_id():
    with pytest.raises(ValueError, match='STARTING_MINIMUM_REQUIRED'):
        scoped_ids([record('x')],{'structure_id':'A','pes_id':'PES-1'},require_starting_minimum=True)


def sha(data):return hashlib.sha256(data).hexdigest()


def write_lean(root,*,ambiguous=False, conflict=False):
    rows={'canonical_minima':[{'parent_structure_id':'A','minimum_id':'mA'}],
          'saddle_registry':[{'canonical_saddle_id':'s1','structure_id':'A','observed_starting_minimum_ids':[]}],
          'reaction_registry':[{'canonical_reaction_id':'r1','structure_id':'A','saddle_group_id':'g1'}],
          'reaction_observations':[{'canonical_reaction_id':'r1','canonical_saddle_id':'s1'},
                                   {'canonical_reaction_id':None,'canonical_saddle_id':None}]}
    if ambiguous:rows['canonical_minima'].append({'parent_structure_id':'A','minimum_id':'mB'})
    if conflict:rows['reaction_registry'][0]['starting_minimum_id']='mB'
    dirs={'canonical_minima':'minima','saddle_registry':'saddles','reaction_registry':'reactions',
          'reaction_observations':'triplets'}
    parts=[]
    for table,records in rows.items():
        rel=f'{dirs[table]}/part-000.jsonl';path=root/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps(x)+'\n' for x in records))
        parts.append({'table':table,'part':rel,'rows':len(records)})
    (root/'manifest.json').write_text(json.dumps({'schema_version':'saddlebench-lean-registry-v1',
                        'source_counts':{k:len(v) for k,v in rows.items()},'parts':parts,
                        'source_registry':'source.sqlite'}))
    (root/'EXPORT_COMPLETE').write_text('PASS\n')


def test_backfill_is_derived_immutable_no_origin_fabrication(tmp_path):
    source=tmp_path/'lean';source.mkdir();write_lean(source)
    source_bytes={(str(x.relative_to(source))):x.read_bytes() for x in source.rglob('*.jsonl')}
    dst=tmp_path/'annotated'; report=build(source,dst)
    assert report['counts']['reaction_registry_annotated']==1
    assert report['counts']['saddle_registry_annotated']==1
    assert report['counts']['reaction_observations_annotated']==1
    assert report['counts']['reaction_observations_unresolved']==1
    rx=json.loads((dst/'reactions/part-000.jsonl').read_text())
    saddle=json.loads((dst/'saddles/part-000.jsonl').read_text())
    assert rx['associated_starting_minimum_id']=='mA'
    assert rx['associated_starting_minimum_id_basis'].startswith('DERIVED_')
    assert 'starting_minimum_id' not in rx
    assert saddle['observed_starting_minimum_ids']==[]
    assert (dst/'PROJECTION_COMPLETE').read_text().strip()=='PASS'
    assert source_bytes=={str(x.relative_to(source)):x.read_bytes() for x in source.rglob('*.jsonl')}
    with pytest.raises(ValueError,match='OUTPUT_ALREADY_EXISTS'):
        build(source,dst)


@pytest.mark.parametrize('ambiguous,conflict,pattern',[(True,False,'AMBIGUOUS_MINIMUM'),(False,True,'EXISTING_STARTING_ID_CONFLICT')])
def test_backfill_fails_closed(tmp_path,ambiguous,conflict,pattern):
    src=tmp_path/'s';src.mkdir();write_lean(src,ambiguous=ambiguous,conflict=conflict)
    with pytest.raises(ValueError,match=pattern):build(src,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_generic_reference_counts_not_pinned(tmp_path):
    base=tmp_path/'ref';(base/'reference').mkdir(parents=True)
    cases=tmp_path/'cases.csv'
    registry='a'*64
    payload=b'{"reaction_id":"one"}\n'
    (base/'reference/reference.jsonl').write_bytes(payload)
    (base/'reference/manifest.json').write_text(json.dumps({'registry_sha256':registry,'reference_sha256':sha(payload),'reference_count':1}))
    (base/'private_manifest.json').write_text(json.dumps({'source_registry_sha256':registry,'case_count':2}))
    with cases.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['case_id','registry_sha256','structure_id','pes_id'])
        writer.writeheader();writer.writerows([{'case_id':f'case{j}','registry_sha256':registry,'structure_id':'A','pes_id':'P'} for j in range(2)])
    checks={'reference/reference.jsonl':sha(payload)}
    (base/'CHECKSUMS.json').write_text(json.dumps(checks))
    assert verify_reference(base,cases) is True
    with cases.open('a') as f:f.write('case3,'+registry+',A,P\n')
    with pytest.raises(RuntimeError,match='PUBLIC_INPUTS_SNAPSHOT_MISMATCH'):
        verify_reference(base,cases)

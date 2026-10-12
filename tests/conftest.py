import pytest
from saddlebench.reference.snapshot import export_snapshot,load_reference
from examples.generate_fixture import frames

@pytest.fixture
def refs(tmp_path):
    records,_=frames()
    target=tmp_path/'reference'
    export_snapshot(records,target,{'reference_version':'synthetic','registry_sha256':'1'*64,
          'registry_watermark':0,'event_cutoff':None,'producer_source_id':'fixture',
          'scientific_contract_id':'SCIENTIFIC_CONTRACT_20261002_v1','scoring_policy_id':'v1',
          'coverage':{'eligible_denominator_complete':True}})
    return load_reference(target)

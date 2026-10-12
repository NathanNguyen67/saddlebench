"""Reproduce the synthetic reference, proposed saddles and expected report (CPU only)."""
from pathlib import Path
import copy
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from saddlebench.reference.snapshot import export_snapshot
from saddlebench.cli import main
SHA_A='a'*64
SHA_B='b'*64
SHA_C='c'*64

def geom(y):
    return {'numbers':[1,6,8], 'positions_angstrom':[[0.,0.,0.],[1.,0.,0.],[2.,float(y),0.]],
            'cell_angstrom':[[10.,0.,0.],[0.,10.,0.],[0.,0.,10.]],'pbc':[False]*3,
            'fixed_indices':[], 'constraints_known':True}

def record(label,y,delta=1.0,order=1,start='min-A',pes='PES-A',protocol='powell_damped_lbfgs',dm=True,energy_pes=None,endpoints=None):
    return {'reference_id':label,'identity_id':'identity-'+label if label!='a-start-B' else 'identity-a',
        'parent_structure_id':'parent-A','starting_minimum_id':start,'pes_id':pes,'geometry':geom(y),
        'energy':{'saddle_ev':-10.+delta,'minimum_ev':-10.,'saddle_pes_id':energy_pes or pes,
                  'minimum_pes_id':pes,'source_sha256':SHA_A},
        'hessian':None if order is None else {'schema':'standalone_hessian_v2','order':order,
                  'negative_tolerance_ev_a2':1e-6,'projection':'canonical-translations',
                  'certified_geometry_id':label,'source_sha256':SHA_B,'verified':True},
        'doublemin':{'executed':dm,'protocol':protocol,'powell_damped':protocol=='powell_damped_lbfgs',
                  'maximum_steps':500,'displacement_angstrom':0.25,'target_fmax_ev_a':0.0025,
                  'certified_mode':True,'source_sha256':SHA_C,
                  'endpoints':endpoints or [{'converged':True,'final_fmax_ev_a':0.002,'matched_minimum_id':start},
                                           {'converged':True,'final_fmax_ev_a':0.002,'matched_minimum_id':'product-A'}]},
        'evidence_sources':{'qualification':'SYNTHETIC_ONLY','producer_event_id':'synthetic-event-'+label,
                            'producer_event_sha256':SHA_A,'event_sequence':0,
                            'published':True},'uncertainty_reasons':[]}

def frames():
    records=[record('a',0.,1.4),record('b',2.,1.5),record('c',4.,3.0),record('d',6.,1.2,order=2),
            record('e',8.,1.2,order=None),record('f',10.,1.2,dm=False),
            record('g',12.,1.2,protocol='ordinary_lbfgs'),record('h',14.,1.2,energy_pes='PES-other'),
            record('i',16.,1.2,endpoints=[{'converged':True,'final_fmax_ev_a':0.002,'matched_minimum_id':'other1'},
                                         {'converged':True,'final_fmax_ev_a':0.002,'matched_minimum_id':'other2'}]),
            record('j',18.,1.2),record('k',18.3,1.2),record('a-start-B',0.,1.1,start='min-B')]
    submits=[('a','a',0.),('duplicate-a','a',0.),('boundary-b','b',2.),('nonfo-d','d',6.),
             ('unknown-e','e',8.),('skipped-f','f',10.),('legacy-g','g',12.),('wrong-pes-h','h',14.),
             ('disconnected-i','i',16.),('ambiguous-j-k','j',18.15),('unmatched','a',35.),
             ('high-c','c',4.),('wrong-start','a',0.)]
    return records,submits

def write_example(root=ROOT/'examples'):
    records,submits=frames()
    target=root/'reference_v1'
    if target.exists():
        import shutil;shutil.rmtree(target)
    export_snapshot(records,target,{'reference_version':'synthetic-fixture-v1',
        'registry_sha256':'1'*64,'registry_watermark':0,'event_cutoff':None,
        'producer_source_id':'synthetic-test-only','scientific_contract_id':'SCIENTIFIC_CONTRACT_20261002_v1',
        'scoring_policy_id':'SCORING_POLICY_20261011_v1',
        'coverage':{'eligible_denominator_complete':True,'producer_mapping':'SYNTHETIC_ONLY',
                    'limitations':['NOT a real registry or LS6 source evidence']}})
    symbols=['H','C','O']
    lines=[]
    for name,key,y in submits:
        if name=='wrong-start':start='unknown-min'
        else:start='min-A'
        lines.append('3')
        lines.append('Properties=species:S:1:pos:R:3 Lattice="10 0 0 0 10 0 0 0 10" pbc="F F F" '
                f'fixed_indices="" submission_id={name} parent_structure_id=parent-A starting_minimum_id={start} pes_id=PES-A')
        for sym,pos in zip(symbols,geom(y)['positions_angstrom']):lines.append(f'{sym} {pos[0]} {pos[1]} {pos[2]}')
    (root/'submissions.extxyz').write_text('\n'.join(lines)+'\n')
    main(['score',str(root/'submissions.extxyz'),'--reference',str(target),'--elapsed-seconds','3600',
          '--out-prefix',str(root/'expected_score')])
    return target
if __name__=='__main__':write_example()

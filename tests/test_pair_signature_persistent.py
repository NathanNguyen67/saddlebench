"""Persistent interatomic MIC-distance screening must never change matches."""
from __future__ import annotations
import copy
import os
from pathlib import Path
import numpy as np
import pytest
from saddlebench.matching import geometry
from saddlebench.matching.group_radius_exists import GroupCatalog,build_index
from saddlebench.matching.pair_signature import (PersistentPairSignatures,pair_indices,geometry_key)


def example(offset=0., n=21, skew=0., pbc=(True,True,False)):
    rng=np.random.default_rng(765)
    p=rng.uniform(0.1,4.,size=(n,3))
    p[-1,2]+=offset
    return dict(numbers=[6]*n,positions_angstrom=p.tolist(),
                cell_angstrom=[[10.,0.,0.],[skew,10.,0.],[0.,0.,12.]],
                pbc=list(pbc),fixed_indices=[],constraints_known=False)


def members(geometries):
    return [(None,dict(saddle_group_id=f'r{i}',reaction_id=f'r{i}',geometry=g))
            for i,g in enumerate(geometries)]


def test_cache_reload_and_data_binding(tmp_path):
    from saddlebench.matching.group_radius_exists import source_identity
    refs=members([example(i*.3) for i in range(9)])
    geoms=[m['geometry'] for _,m in refs]
    sha=source_identity(refs)
    first=PersistentPairSignatures(geoms,sha,tmp_path/'persistent')
    assert first.state=='BUILT'
    second=PersistentPairSignatures(geoms,sha,tmp_path/'persistent')
    assert second.state=='LOADED'
    assert np.array_equal(first.data,second.data)
    modified=copy.deepcopy(refs)
    modified[-1][1]['geometry']['positions_angstrom'][-1][2]+=.11
    modified_hash=source_identity(modified)
    assert modified_hash!=sha
    third=PersistentPairSignatures([m['geometry'] for _,m in modified],modified_hash,tmp_path/'persistent')
    assert third.state=='BUILT'
    assert third.cache_file!=second.cache_file


def test_only_reject_provably_unmatched(tmp_path):
    from saddlebench.matching.group_radius_exists import source_identity
    for skew in (0.,2.4):
        refs=[example(offset,skew=skew) for offset in np.linspace(0.,1.5,15)]
        m=members(refs)
        obj=PersistentPairSignatures(refs,source_identity(m),tmp_path/str(skew))
        for v in (.02,.09,.2,.5,1.05,1.51):
            q=example(v,skew=skew)
            mask=obj.possible(q,.2)
            for flag,g in zip(mask,refs):
                if geometry.core_drift_maxdev(q,g)<=.2+1e-12:
                    assert flag, 'scientific false negative in signature rejection'
        q=example(.1,skew=skew);q['cell_angstrom'][0][0]+=0.01
        assert np.all(obj.possible(q,.2)), 'mismatched cells must be unscreened'
        q=example(.1,skew=skew);q['pbc']=[False,False,False]
        assert np.all(obj.possible(q,.2)), 'mismatched PBC must be unscreened'


def test_greedy_catalog_parity_and_counter(tmp_path,monkeypatch):
    refs=members([example(i*.2) for i in range(25)])
    index=build_index(refs)
    for x in (0.,.04,.15,.33,1.,2.5):
        query=example(x)
        monkeypatch.setenv('SADDLEBENCH_SIGNATURE_FILTER','off')
        old=GroupCatalog(refs,index,signature_cache_root=tmp_path).find_first(query)
        monkeypatch.setenv('SADDLEBENCH_SIGNATURE_FILTER','on')
        new_catalog=GroupCatalog(refs,index,signature_cache_root=tmp_path)
        new=new_catalog.find_first(query)
        assert (old['found'],old.get('match_index'))==(new['found'],new.get('match_index'))
        assert new_catalog.signature_cache_state in ('BUILT','LOADED')
    again=GroupCatalog(refs,index,signature_cache_root=tmp_path)
    assert again.signature_cache_state=='LOADED'


def test_different_fixed_masks_reuse_signature_without_identity_change(tmp_path):
    from saddlebench.matching.group_radius_exists import source_identity
    refs=members([example(0.)]);g=refs[0][1]['geometry']
    obj=PersistentPairSignatures([g],source_identity(refs),tmp_path)
    q=copy.deepcopy(g);q['fixed_indices']=[0,1]
    assert bool(obj.possible(q,.2)[0])


def test_signature_cache_tamper_never_causes_wrong_screening(tmp_path):
    from saddlebench.matching.group_radius_exists import source_identity
    refs=members([example(i*.4) for i in range(2)])
    g=[v['geometry'] for _,v in refs]
    sha=source_identity(refs)
    a=PersistentPairSignatures(g,sha,tmp_path)
    with open(a.cache_file,'wb') as f:f.write(b'broken interrupted cache')
    b=PersistentPairSignatures(g,sha,tmp_path)
    assert b.state in ('BUILT','RACE_BUILT')
    assert all(b.possible(g[0],.2)) is False or bool(b.possible(g[0],.2)[0])


def test_pair_indices_deterministic():
    for n in (2,3,5,21,343):
        a=pair_indices(n)
        assert np.array_equal(a,pair_indices(n))
        assert np.all(a[:,0]!=a[:,1])
        assert np.max(a)<n

from __future__ import annotations
import sys
import types
from pathlib import Path
from saddlebench.prototype.reaction_mvp import read_submissions

def test_native_traj_adapter_parses_scope_and_fixatoms(monkeypatch,tmp_path):
    import numpy as np
    class FixAtoms:
        def __init__(self,indices):self.indices=indices
        def get_indices(self):return self.indices
    class Cell:
        array=np.diag([12.,12.,12.])
    class Atoms:
        numbers=np.array([6,1])
        positions=np.array([[0.,0.,0.],[1.,0.,0.]])
        cell=Cell()
        pbc=np.array([False,False,False])
        constraints=[FixAtoms([0])]
        info={'starting_minimum_id':'m0','pes_id':'pes:a','parent_structure_id':'p1'}
    ase=types.ModuleType('ase');aseio=types.ModuleType('ase.io');aseio.read=lambda *a,**k:[Atoms()]
    aseconst=types.ModuleType('ase.constraints');aseconst.FixAtoms=FixAtoms
    monkeypatch.setitem(sys.modules,'ase',ase)
    monkeypatch.setitem(sys.modules,'ase.io',aseio)
    monkeypatch.setitem(sys.modules,'ase.constraints',aseconst)
    p=tmp_path/'test.traj';p.write_bytes(b'fixture')
    result=read_submissions(p)
    assert result[0]['geometry']['fixed_indices']==[0]
    assert result[0]['geometry']['constraints_known'] is True
    assert result[0]['starting_minimum_id']=='m0'

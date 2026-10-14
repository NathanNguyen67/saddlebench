"""Experimental hierarchical exact relative-coordinate metric ball index.

All numerical pruning is guarded by the frozen periodic MIC and the proven
inequality d_relative/2 <= canonical maxdev. This is NOT saddle-group identity;
1.6/0.8/0.4/0.2/0.1 are computational METRIC radii in Angstrom. A query
outside a ball by more than 0.2 A cannot match ANY geometry in the ball.
"""
from __future__ import annotations
from collections import defaultdict
import numpy as np
from saddlebench.matching.geometry import _mic

RADII=(1.6,0.8,0.4,0.2,0.1)


def _key(g):
    try:
        p=np.asarray(g['positions_angstrom'],float)
        c=np.asarray(g['cell_angstrom'],float)
        if (p.ndim!=2 or p.shape!=(len(g['numbers']),3) or len(p)<2 or len(p)>1024 or
            c.shape!=(3,3) or not np.isfinite(p).all() or not np.isfinite(c).all() or
            np.max(abs(p))>=1e5 or np.max(abs(c))>=1e5):return None
        return tuple(g['numbers']),tuple(g['pbc']),tuple(c.ravel())
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):return None


def relative_metric(a,b):
    pa=np.asarray(a['positions_angstrom'],float)
    pb=np.asarray(b['positions_angstrom'],float)
    delta=pb-pa
    relative=delta[1:]-delta[0]
    return .5*float(np.max(np.linalg.norm(_mic(relative,np.asarray(a['cell_angstrom'],float),a['pbc']),axis=1)))


class Node:
    def __init__(self, records,geoms,depth=0):
        self.center=geoms[records[0]]
        self.entries=tuple(records)
        self.children=()
        self.radius=float('inf')
        try:
            distances=[relative_metric(self.center,geoms[i]) for i in records]
            if all(np.isfinite(distances)):
                self.radius=max(distances,default=0.)+1e-7
            else:return
            if len(records)<=8 or depth>=len(RADII):return
            target=RADII[depth]
            if self.radius<=target and depth==len(RADII)-1:return
            # Subdivide deterministically without relying on chemistry/topology.
            # Only partition membership here; actual radius is verified by MIC.
            subset=np.asarray([geoms[i]['positions_angstrom'] for i in records],float)
            rel=subset[:,1:,:]-subset[:,:1,:]
            data=rel.reshape(len(records),-1)
            if data.shape[1]==0:return
            variances=np.var(data,axis=0)
            ax=int(np.argmax(variances))
            order=np.argsort(data[:,ax],kind='stable')
            midpoint=len(order)//2
            if midpoint<1 or midpoint==len(order):return
            left=[records[j] for j in order[:midpoint]]
            right=[records[j] for j in order[midpoint:]]
            self.children=(Node(left,geoms,depth+1),Node(right,geoms,depth+1))
        except (ValueError,TypeError,OverflowError,np.linalg.LinAlgError,FloatingPointError):
            # Uncertifiable node never prunes. No scientific omission.
            self.radius=float('inf');self.children=()

    def query(self,g,t,chosen,stats):
        stats['tree_nodes']+=1
        try:d=relative_metric(g,self.center)
        except (ValueError,TypeError,OverflowError,np.linalg.LinAlgError,FloatingPointError):d=0.
        if d > self.radius+t+1e-9:
            stats['pruned_refs']+=len(self.entries)
            return
        if self.children:
            for child in self.children:child.query(g,t,chosen,stats)
        else:chosen.update(self.entries)

class Hierarchy:
    def __init__(self,entries,geometry_of=lambda x:x['geometry']):
        self.size=len(entries);self.trees={};self.stats={'tree_nodes':0,'pruned_refs':0,'query_refs':0,'survivors':0}
        groups=defaultdict(list)
        for i,item in enumerate(entries):
            try:g=geometry_of(item);k=_key(g)
            except (KeyError,ValueError,TypeError):k=None
            if k is not None:groups[k].append((i,g))
        for k,items in groups.items():
            if len(items)<16:continue
            geoms={i:g for i,g in items}
            try:self.trees[k]=Node([i for i,_ in items],geoms)
            except (ValueError,TypeError,OverflowError,np.linalg.LinAlgError,FloatingPointError):pass
    def possible(self,g,t):
        keep=np.ones(self.size,dtype=bool)
        self.stats['query_refs']+=self.size
        k=_key(g)
        if t==0.2 and k in self.trees:
            tree=self.trees[k];good=set()
            tree.query(g,t,good,self.stats)
            keep[list(tree.entries)]=False
            if good:keep[list(good)]=True
        self.stats['survivors']+=int(np.count_nonzero(keep))
        return keep

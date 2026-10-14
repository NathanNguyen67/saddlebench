"""Exact, scope-preserving, group-radius existence queries (experimental).

Only the certified periodic relative-coordinate metric is used for group
rejection. The scientific match is *always* frozen core-drift maxdev <=0.2 A.
A pinned registry group ID is a partition label, NEVER proof of its radius.
All radii are computed from the geometries, and incompatible keys are not pruned.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import hashlib,json,math
import numpy as np
from saddlebench.matching import geometry as geom
from saddlebench.matching.metric_groups import relative_metric
from saddlebench.matching.batch_exact import _measure_packed, _geom_key

T=0.2
GUARD=1e-7
SCHEMA='saddlebench-canonical-group-radius-exists-experiment-v1'


def groups_from_members(members):
    """This partition is never transitive clustering; use exact registry IDs only.

    For missing IDs, do not group distinct references. Identical geometric keys
    are necessary for a certified group-radius calculation, not a new scope.
    """
    groups=defaultdict(list)
    for i,(_,member) in enumerate(members):
        key=_geom_key(member['geometry'])
        gid=member.get('saddle_group_id')
        label=('registry',str(gid)) if gid is not None else ('single',i)
        # Split by exact cell/PBC/atom ordering. None geometry is a singleton.
        geomkey=key if key is not None else ('noncertified',i)
        groups[(label,geomkey)].append(i)
    return list(groups.values())


def source_identity(members):
    raw=json.dumps([{'group_id':m.get('saddle_group_id'),
        'geometry':m['geometry']} for _,m in members],sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('utf8')
    return hashlib.sha256(raw).hexdigest()


def build_index(members,center_mode='first'):
    if center_mode not in ('first','best3'):raise ValueError('INVALID_CENTER_MODE')
    geom=[m['geometry'] for _,m in members]
    groups=[];counter=Counter()
    for indices in groups_from_members(members):
        compatible=(len(indices)>1 and _geom_key(geom[indices[0]]) is not None)
        candidates=([indices[0]] if center_mode=='first' else
                    list(dict.fromkeys([indices[0],indices[len(indices)//2],indices[-1]])))
        best=None
        for center in candidates if compatible else [indices[0]]:
            try:
                # Never substitute maxdev or an assumed 0.1 radius for this
                # true periodic relative-coordinate metric cover radius.
                radius=max((relative_metric(geom[center],geom[j]) for j in indices),default=0.)
                if not math.isfinite(radius):raise ValueError('NONFINITE_RADIUS')
                radius+=GUARD
            except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
                radius=None
            if radius is None:
                best=None;break
            if best is None or radius<best[1]:best=(center,radius)
        if best is None:
            center=indices[0];radius=None;counter['uncertified_groups']+=1
        else:center,radius=best
        groups.append({'members':indices,'center':center,'radius':radius})
        counter['groups']+=1;counter['members']+=len(indices)
        counter['singletons']+=int(len(indices)==1)
        counter['multi_member_groups']+=int(len(indices)>1)
    return {'schema':SCHEMA,'center_mode':center_mode,'source_sha256':source_identity(members),
            'groups':groups,'counts':dict(counter)}


def validate_index(index,members):
    if (index.get('schema')!=SCHEMA or index.get('source_sha256')!=source_identity(members)):
        raise ValueError('GROUP_INDEX_SOURCE_CHANGED')
    all_indices=[]
    geom=[m['geometry'] for _,m in members]
    for group in index['groups']:
        ids=group['members'];center=group['center'];radius=group['radius']
        if (not isinstance(ids,list) or not ids or any(type(x)!=int for x in ids)
                or center not in ids):raise ValueError('GROUP_INDEX_MEMBERS_INVALID')
        if radius is not None and (not isinstance(radius,(float,int)) or not math.isfinite(radius)
                                   or radius<GUARD):raise ValueError('GROUP_INDEX_RADIUS_INVALID')
        # The provenance hash certifies the input geometry, but also ensure
        # partition membership is exactly the expected canonical group set.
        all_indices.extend(ids)
    if sorted(all_indices)!=list(range(len(members))):raise ValueError('GROUP_INDEX_PARTITION_INVALID')
    expected={frozenset(g) for g in groups_from_members(members)}
    if {frozenset(g['members']) for g in index['groups']}!=expected:
        raise ValueError('GROUP_INDEX_REGISTRY_PARTITION_CHANGED')
    return True


class GroupCatalog:
    """Group-prune, order by representative, exact microbatch, exit on first hit."""
    def __init__(self,members,index):
        validate_index(index,members)
        self.geoms=[m['geometry'] for _,m in members]
        self.index=index
        self.keys=[_geom_key(g) for g in self.geoms]
        self.anchor=geom.BatchedAnchorFilter(self.geoms,lambda g:g)
        self.features=[]
        for g in self.geoms:
            try:
                p=np.asarray(g['positions_angstrom'],float)
                loc=sorted({0,len(p)//4,len(p)//2,3*len(p)//4,len(p)-1})
                self.features.append((p[loc]-p[0]).reshape(-1))
            except (ValueError,KeyError,TypeError,IndexError):self.features.append(None)

    def find_first(self,query,*,batch_size=16,order='near',member_order='original',group_prune=True):
        if not 1<=batch_size<=256:raise ValueError('BATCH_OUT_OF_RANGE')
        if order not in ('original','near'):raise ValueError('ORDER_INVALID')
        if member_order not in ('original','anchor_rank'):raise ValueError('MEMBER_ORDER_INVALID')
        if not self.geoms:return {'found':False,'exact':0,'batch_calls':0,'mic_calls':0,'groups_rejected':0,'groups_tested':0,'anchor_survivors':0,'candidate_refs':0,'fallback':0,'uncertified':0}
        anchor=self.anchor.possible(query,T)
        # Run one vectorized anchor filter per query, then cheap group-radius
        # checks. No exact MIC is spent on a certified rejected group.
        key=_geom_key(query)
        queue=[];stats=Counter({'anchor_survivors':int(np.count_nonzero(anchor))})
        for group in self.index['groups']:
            ids=group['members']
            viable=[i for i in ids if anchor[i]]
            if not viable:stats['groups_rejected_anchor']+=1;continue
            center=group['center'];radius=group['radius'];d=None
            if group_prune and radius is not None and key is not None and self.keys[center]==key:
                try:
                    d=relative_metric(query,self.geoms[center])
                    stats['rep_metric_calls']+=1
                    if d>float(radius)+T+GUARD:
                        stats['groups_rejected']+=1
                        stats['refs_rejected_group']+=len(viable)
                        continue
                except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
                    stats['uncertified']+=1
            else:
                stats['uncertified']+=1
            queue.append((float(d) if d is not None else float('inf'),ids[0],viable,center))
        if order=='near':queue.sort(key=lambda entry:(entry[0],entry[1]))
        # For candidate ranking only: no scientific pruning by raw coordinate differences.
        try:
            qpos=np.asarray(query['positions_angstrom'],float)
            loc=sorted({0,len(qpos)//4,len(qpos)//2,3*len(qpos)//4,len(qpos)-1})
            qfeature=(qpos[loc]-qpos[0]).reshape(-1)
        except (ValueError,KeyError,TypeError,IndexError):qfeature=None
        qarr=None
        if key is not None:qarr=np.asarray([query['positions_angstrom']],dtype=np.float64)
        # count genuine exact evaluations, including speculative microbatch work
        for _,_,viable,center in queue:
            stats['groups_tested']+=1
            if member_order=='anchor_rank' and qfeature is not None:
                viable.sort(key=lambda i:(float(np.max(np.abs(self.features[i]-qfeature)))
                    if self.features[i] is not None and self.features[i].shape==qfeature.shape else float('inf'),i))
            else:
                # representative first improves odds of an immediate group hit
                if center in viable:viable.remove(center);viable.insert(0,center)
            stats['candidate_refs']+=len(viable)
            at=0
            while at<len(viable):
                j=viable[at]
                if key is None or self.keys[j]!=key:
                    stats['exact']+=1;stats['fallback']+=1
                    try:
                        if geom.core_drift_maxdev(query,self.geoms[j])<=T+1e-12:
                            stats['found']+=1;stats['match_index']=j;return dict(stats)
                    except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
                        stats['uncertain_scalar']+=1
                    at+=1;continue
                block=[j];at+=1
                while at<len(viable) and len(block)<batch_size and self.keys[viable[at]]==key:
                    block.append(viable[at]);at+=1
                if len(block)==1 or batch_size==1:
                    stats['exact']+=1;stats['fallback']+=1
                    try:
                        if geom.core_drift_maxdev(query,self.geoms[j])<=T+1e-12:
                            stats['found']+=1;stats['match_index']=j;return dict(stats)
                    except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
                        stats['uncertain_scalar']+=1
                    continue
                try:
                    ra=np.asarray([self.geoms[i]['positions_angstrom'] for i in block],float)
                    ids=[(0,i) for i in range(len(block))]
                    cell=np.asarray(query['cell_angstrom'],float)
                    from saddlebench.matching import batch_exact
                    mic_before=batch_exact._STATS['mic_calls']
                    distances=_measure_packed(ids,qarr,ra,cell,query['pbc'])
                    stats['mic_calls']+=batch_exact._STATS['mic_calls']-mic_before
                    if len(distances)!=len(block) or not np.isfinite(distances).all():raise ValueError('BAD_BATCH')
                except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
                    for ii in block:
                        stats['exact']+=1;stats['fallback']+=1
                        try:
                            if geom.core_drift_maxdev(query,self.geoms[ii])<=T+1e-12:
                                stats['found']+=1;stats['match_index']=ii;return dict(stats)
                        except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
                            stats['uncertain_scalar']+=1
                    continue
                stats['exact']+=len(block);stats['batch_calls']+=1;stats['packed']+=len(block)
                for ii,dist in zip(block,distances):
                    if abs(float(dist)-T)<1e-8:
                        stats['exact']+=1;stats['fallback']+=1
                        try:
                            if geom.core_drift_maxdev(query,self.geoms[ii])<=T+1e-12:
                                stats['found']+=1;stats['match_index']=ii;return dict(stats)
                        except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
                            stats['uncertain_scalar']+=1
                    elif float(dist)<=T:
                        stats['found']+=1;stats['match_index']=ii;return dict(stats)
        return dict(stats)

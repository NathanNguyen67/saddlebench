"""Compact global candidate packing for the verified A+B Saskatchewan prototype.

B's conservative masks choose survivors. Compatible survivors across different
metric-tree leaves and different query rows are packed into dense outer batches.
Canonical skew MIC is still called in bounded DISPLACEMENT chunks (<150000
image products) so large outer batches never trigger its scalar QR fallback.
Any numerical uncertainty or incompatible geometry falls back to the frozen W2
scalar geometry path; exact score equality remains mandatory.
"""
from __future__ import annotations
import os
import json
from pathlib import Path
from collections import defaultdict
import numpy as np
from saddlebench.matching import geometry as geom

_STATS = dict(batch_pairs=0, fallback_pairs=0, batch_calls=0,
              outer_batches=0, mic_calls=0, mic_displacement_chunks=0,
              candidate_pairs=0, compacted_pairs=0, W2_candidates=0,
              hierarchy_candidates=0, keys=0, mic_failed_batches=0, group_metric_calls=0, group_refs_rejected=0, group_groups_rejected=0, group_groups_untestable=0)


def get_stats():
    return dict(_STATS)


def _geom_key(g):
    try:
        cell=np.asarray(g['cell_angstrom'],dtype=np.float64)
        pos=np.asarray(g['positions_angstrom'],dtype=np.float64)
        n=len(g['numbers'])
        if (cell.shape!=(3,3) or pos.shape!=(n,3) or n<2 or n>1024 or
            not np.isfinite(cell).all() or not np.isfinite(pos).all() or
            np.max(np.abs(pos))>=1e5 or np.max(np.abs(cell))>=1e5):
            return None
        return tuple(g['numbers']),tuple(g['pbc']),tuple(cell.ravel())
    except (ValueError,TypeError,IndexError,KeyError,OverflowError):
        return None


def _mic_in_chunks(displacements, cell, pbc, atom_count):
    """Apply frozen MIC, never crossing its 150000 image-evaluation limit.

    Chunking at whole geometry boundaries avoids splitting any one geometry's
    median selection. MIC itself only needs separate displacement vectors.
    """
    n=len(displacements)
    if not n:return np.empty_like(displacements)
    # Same certified radius-1 budget used in A; preserve sub-4500 memory bound.
    size=max(atom_count, (4500//atom_count)*atom_count)
    result=np.empty_like(displacements)
    for lo in range(0,n,size):
        hi=min(lo+size,n)
        value=geom._mic(displacements[lo:hi],cell,pbc)
        if value.shape!=(hi-lo,3):raise ValueError('MIC_SHAPE_CHANGED')
        result[lo:hi]=value
        _STATS['mic_calls']+=1
        _STATS['mic_displacement_chunks']+=1
    return result


def _measure_packed(ids, qa, ra, cell, pbc):
    """Return vector of exact candidate distances, with canonical fallback on error."""
    q_ids=np.fromiter((v[0] for v in ids),dtype=np.intp,count=len(ids))
    r_ids=np.fromiter((v[1] for v in ids),dtype=np.intp,count=len(ids))
    n=qa.shape[1]
    delta=ra[r_ids]-qa[q_ids]
    shape=delta.shape
    mic=_mic_in_chunks(delta.reshape(-1,3),cell,pbc,n).reshape(shape)
    magnitudes=np.linalg.norm(mic,axis=2)
    core=magnitudes<=np.median(magnitudes,axis=1)[:,None]
    denom=np.sum(core,axis=1)
    if np.any(denom==0):raise ValueError('EMPTY_CORE')
    drift=np.sum(np.where(core[:,:,None],mic,0.),axis=1)/denom[:,None]
    fixed=_mic_in_chunks((mic-drift[:,None,:]).reshape(-1,3),cell,pbc,n).reshape(shape)
    return np.max(np.linalg.norm(fixed,axis=2),axis=1)



_GROUP_CACHE = {}


def _validated_scope_groups(scope, members):
    """Read pinned, measured canonical group index; never infer a radius from label."""
    from saddlebench.matching.ab_store import scope_id, geometry_digest
    from saddlebench.matching.group_radius_exists import validate_index
    cache_path=os.environ.get('SADDLEBENCH_GROUP_INDEX_FILE')
    if not cache_path:raise ValueError('GROUP_SCORE_CACHE_FILE_REQUIRED')
    if cache_path not in _GROUP_CACHE:
        data=json.loads(Path(cache_path).read_text())
        if data.get('schema')!='saddlebench-group-cache-v1' or data.get('mode')!='first':
            raise ValueError('GROUP_SCORE_CACHE_SCHEMA_INVALID')
        expected=os.environ.get('SADDLEBENCH_REFERENCE_SHA256')
        if not expected or data.get('refsha')!=expected:
            raise ValueError('GROUP_SCORE_CACHE_REFERENCE_SHA_CHANGED')
        _GROUP_CACHE[cache_path]=data
    section=_GROUP_CACHE[cache_path]['scopes'].get(scope_id(scope))
    if section is None:raise ValueError('GROUP_SCORE_SCOPE_MISSING')
    if section['geometry_sha256']!=geometry_digest(members):
        raise ValueError('GROUP_SCORE_GEOMETRY_CHANGED')
    idx=section['index']
    validate_index(idx,members)
    return idx['groups']


def _prune_canonical_groups(query, members, w2mask, groups):
    """Safe metric-ball rejection only. Keep all possible members for full scoring.

    Relative-metric d(query,member) <= core-drift maxdev(query,member).
    For group center c, member m: d(q,m) >= d(q,c)-d(c,m).
    Therefore no canonical match exists if d(q,c)>0.2+radius+guard.
    """
    from saddlebench.matching.metric_groups import relative_metric
    mask=w2mask.copy()
    qkey=_geom_key(query)
    for group in groups:
        ids=group['members']
        if not np.any(mask[ids]):continue
        center=group['center'];radius=group['radius']
        if radius is None or qkey is None or qkey!=_geom_key(members[center][1]['geometry']):
            _STATS['group_groups_untestable']+=1
            continue
        try:
            distance=relative_metric(query,members[center][1]['geometry'])
            _STATS['group_metric_calls']+=1
            if np.isfinite(distance) and distance>0.2+float(radius)+1e-7:
                _STATS['group_refs_rejected']+=int(np.count_nonzero(mask[ids]))
                _STATS['group_groups_rejected']+=1
                mask[ids]=False
        except (ValueError,TypeError,OverflowError,IndexError,np.linalg.LinAlgError,FloatingPointError):
            _STATS['group_groups_untestable']+=1
    return mask

def precompute_matrix(submissions,by_scope,metric_hierarchies=None):
    """Return {query_index: (safe W2+B mask, per-ref fast exact distances)}.

    The hierarchy *never* terminates at its first match; every eligible member
    and ambiguous reaction identity remains represented. Unsafe input geometry
    isn't packed and falls back to the original scalar path.
    """
    from saddlebench.matching.geometry import BatchedAnchorFilter
    if metric_hierarchies is None:raise ValueError('METRIC_HIERARCHIES_REQUIRED')
    target=int(os.environ.get('SADDLEBENCH_PACKED_PAIRS','512'))
    if not 16<=target<=4096:raise ValueError('PACKED_PAIRS_OUT_OF_RANGE')
    grouped=defaultdict(list)
    for qi,sub in enumerate(submissions):
        g=sub['geometry']
        scope=(sub['parent_structure_id'],sub['starting_minimum_id'],sub['pes_id'],tuple(g['numbers']))
        grouped[scope].append(qi)
    result={}
    for scope,qids in grouped.items():
        members=by_scope.get(scope,[])
        refs=[m['geometry'] for _,m in members]
        filt=BatchedAnchorFilter(members,lambda pair:pair[1]['geometry'])
        hierarchy=metric_hierarchies.get(scope)
        if hierarchy is None:raise ValueError('INDEX_SCOPE_NOT_FOUND')
        method=os.environ.get('SADDLEBENCH_MATCHER_INDEX','ab')
        if method not in ('ab','group'):raise ValueError('UNKNOWN_MATCHER_INDEX_METHOD')
        groups=_validated_scope_groups(scope,members) if method=='group' and members and 'record' not in members[0][1] else None
        # A mask belongs to the specific prediction. Grouping or packing NEVER
        # alters its membership, its native reaction order or its score status.
        for qi in qids:
            qg=submissions[qi]['geometry']
            w2=filt.possible(qg,0.2)
            _STATS['W2_candidates']+=int(np.count_nonzero(w2))
            if groups is not None:
                mask=_prune_canonical_groups(qg,members,w2,groups)
            else:
                mask=w2 & hierarchy.possible(qg,0.2)
            _STATS['hierarchy_candidates']+=int(np.count_nonzero(mask))
            result[qi]=(mask,{})
        ref_buckets=defaultdict(list)
        for ri,g in enumerate(refs):
            k=_geom_key(g)
            if k is not None:ref_buckets[k].append(ri)
        query_buckets=defaultdict(list)
        for qi in qids:
            k=_geom_key(submissions[qi]['geometry'])
            if k is not None:query_buckets[k].append(qi)
        for key,qgroup in query_buckets.items():
            rgroup=ref_buckets.get(key)
            if not rgroup:continue
            _STATS['keys']+=1
            # Reindex the exact same cell, atom order, and PBC. The packed
            # matrix is local to this compatible cell bucket, not a new scope.
            qa=np.asarray([submissions[qi]['geometry']['positions_angstrom'] for qi in qgroup],dtype=np.float64)
            ra=np.asarray([refs[ri]['positions_angstrom'] for ri in rgroup],dtype=np.float64)
            ref_indices=np.asarray(rgroup,dtype=np.intp)
            pairs=[]
            for local_q,qi in enumerate(qgroup):
                eligible=np.flatnonzero(result[qi][0][ref_indices])
                _STATS['candidate_pairs']+=int(len(eligible))
                pairs.extend((local_q,int(r)) for r in eligible)
            _STATS['compacted_pairs']+=len(pairs)
            cell=np.asarray(submissions[qgroup[0]]['geometry']['cell_angstrom'],dtype=np.float64)
            pbc=submissions[qgroup[0]]['geometry']['pbc']
            for begin in range(0,len(pairs),target):
                block=pairs[begin:begin+target]
                _STATS['outer_batches']+=1
                _STATS['batch_calls']+=1
                try:
                    distances=_measure_packed(block,qa,ra,cell,pbc)
                    if not np.all(np.isfinite(distances)):raise ValueError('NONFINITE_BATCH')
                    for (q_local,r_local),dist in zip(block,distances):
                        distance=float(dist)
                        # Frozen comparator handles all comparisons near 0.2 A.
                        if abs(distance-0.2)<1e-8:continue
                        qi=qgroup[q_local];ri=rgroup[r_local]
                        result[qi][1][ri]=(True,distance)
                        _STATS['batch_pairs']+=1
                except (ValueError,TypeError,IndexError,OverflowError,
                        np.linalg.LinAlgError,FloatingPointError):
                    _STATS['mic_failed_batches']+=1
                    # Every pair remains unprocessed in the dictionary, so the
                    # original scalar comparison gets it during score assembly.
                    continue
    _STATS['fallback_pairs']=sum(int(np.count_nonzero(mask))-len(values) for mask,values in result.values())
    return result

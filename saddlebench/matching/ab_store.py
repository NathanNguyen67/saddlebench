"""Deterministic, provenance-checked serialization of the certified metric-tree index.

Never stores or changes scoring identities. Serialized metric balls are independently
constructed from the exact reference snapshot and only prune when their certified
lower bound excludes a 0.2 A canonical core-drift match.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from saddlebench.matching import metric_groups as mg

SCHEMA='saddlebench-ab-relative-metric-index-v1'


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def geometry_digest(members):
    # hash exact geometry payload and relative ordering; registry reaction identity
    # isn't part of metric tree but is kept in the W2 scored reference.
    return _digest(json.dumps([m['geometry'] for r,m in members],sort_keys=True,separators=(',',':'),allow_nan=False).encode())


def scope_id(scope):
    return json.dumps([scope[0],scope[1],scope[2],list(scope[3])],sort_keys=True,separators=(',',':'))


def _keystr(key):
    return json.dumps([list(key[0]),list(key[1]),list(key[2])],separators=(',',':'),allow_nan=False)


def _node_dump(node):
    return {'center_idx':node.entries[0], 'entries':list(node.entries),
            'radius':(node.radius if math.isfinite(node.radius) else None),'children':[_node_dump(x) for x in node.children]}


def _node_load(data,geoms,allowed,depth=0):
    if depth>16:raise ValueError('INDEX_DEPTH_INVALID')
    records=data['entries'];idx=data['center_idx'];r=data['radius']
    if r is None:r=float('inf')
    if not isinstance(records,list) or not records or set(records)-allowed or idx!=records[0]:
        raise ValueError('INDEX_MEMBERS_INVALID')
    if not isinstance(r,(int,float)) or math.isnan(r) or r<0:raise ValueError('INDEX_RADIUS_INVALID')
    node=object.__new__(mg.Node)
    node.center=geoms[idx];node.entries=tuple(records);node.radius=float(r)
    node.children=tuple(_node_load(c,geoms,allowed,depth+1) for c in data['children'])
    if node.children:
        combined=[i for child in node.children for i in child.entries]
        if sorted(combined)!=sorted(node.entries) or len(combined)!=len(node.entries):
            raise ValueError('INDEX_PARTITION_INVALID')
    return node


def dump_hierarchy(h):
    return {'size':h.size,'trees':[{'key':_keystr(k),'node':_node_dump(node)} for k,node in sorted(h.trees.items(),key=lambda kv:_keystr(kv[0]))]}


def load_hierarchy(data,members):
    h=object.__new__(mg.Hierarchy)
    h.size=len(members)
    if data['size']!=h.size:raise ValueError('INDEX_SIZE_MISMATCH')
    h.stats={'tree_nodes':0,'pruned_refs':0,'query_refs':0,'survivors':0}
    h.trees={};geoms={i:m['geometry'] for i,(_,m) in enumerate(members)}
    for entry in data['trees']:
        p=json.loads(entry['key'])
        key=(tuple(p[0]),tuple(p[1]),tuple(p[2]))
        if key in h.trees:raise ValueError('INDEX_DUPLICATE_CELL_KEY')
        indices={i for i,g in geoms.items() if mg._key(g)==key}
        if len(indices)<16:raise ValueError('INDEX_TREE_KEY_INVALID')
        tree=_node_load(entry['node'],geoms,indices)
        if set(tree.entries)!=indices or len(tree.entries)!=len(indices):
            raise ValueError('INDEX_ROOT_MEMBERS_INVALID')
        h.trees[key]=tree
    return h


def reference_digest(reference):
    p=Path(reference)/'reference.jsonl'
    return _digest(p.read_bytes())


def grouped_reactions(reactions):
    from collections import defaultdict
    groups=defaultdict(list)
    for r in reactions:
        for m in r['members']:
            key=(r['structure_id'],r['starting_minimum_id'],r['pes_id'],tuple(m['geometry']['numbers']))
            groups[key].append((r,m))
    return groups


def build_payload(reference,reactions):
    by_scope=grouped_reactions(reactions)
    scopes={}
    for scope,members in sorted(by_scope.items(),key=lambda pair:scope_id(pair[0])):
        hierarchy=mg.Hierarchy(members,lambda pair:pair[1]['geometry'])
        scopes[scope_id(scope)]={'digest':geometry_digest(members),
                                 'tree':dump_hierarchy(hierarchy)}
    return {'schema':SCHEMA,'reference_sha256':reference_digest(reference),
            'scopes':scopes,'metric':'0.5*max_i MIC(relative_position_i) in identical periodic cell',
            'radii_A':[1.6,.8,.4,.2,.1]}


def load_payload(index_file,reference,reactions):
    path=Path(index_file)
    raw=path.read_bytes()
    data=json.loads(raw)
    if data.get('schema')!=SCHEMA:raise ValueError('INDEX_SCHEMA_INVALID')
    if data.get('reference_sha256')!=reference_digest(reference):raise ValueError('INDEX_REFERENCE_SHA_CHANGED')
    by_scope=grouped_reactions(reactions)
    if set(data['scopes'])!={scope_id(s) for s in by_scope}:raise ValueError('INDEX_SCOPE_SET_MISMATCH')
    result={}
    for scope,members in by_scope.items():
        item=data['scopes'][scope_id(scope)]
        if item['digest']!=geometry_digest(members):raise ValueError('INDEX_GEOMETRY_DIGEST_CHANGED')
        result[scope]=load_hierarchy(item['tree'],members)
    return result

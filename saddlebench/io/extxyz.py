"""Strict minimal extxyz parser: complete frame metadata, ordered geometry, no ASE required."""
from __future__ import annotations
import shlex
from pathlib import Path
from saddlebench.reference.snapshot import validate_geometry

SYMBOLS='H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og'.split()
ATOMIC={k:i+1 for i,k in enumerate(SYMBOLS)}

def _bool(s):
    if s in ('T','True','true','1'):return True
    if s in ('F','False','false','0'):return False
    raise ValueError('INVALID_BOOLEAN')

def _keyvals(comment):
    result={}
    for item in shlex.split(comment):
        if '=' not in item:raise ValueError('INVALID_EXTXYZ_HEADER_TOKEN: '+item)
        key,val=item.split('=',1)
        if not key or key in result:raise ValueError('DUPLICATE_OR_EMPTY_EXTXYZ_KEY')
        result[key]=val
    return result

def _properties(text):
    tokens=text.split(':')
    if len(tokens)%3:raise ValueError('INVALID_EXTXYZ_PROPERTIES')
    specs=[];offset=0
    for i in range(0,len(tokens),3):
        name,typ,count=tokens[i:i+3]
        if typ not in ('R','I','S','L') or not count.isdigit() or int(count)<1:raise ValueError('INVALID_EXTXYZ_PROPERTY')
        specs.append((name,typ,int(count),offset));offset+=int(count)
    return specs,offset

def read_extxyz(path):
    rows=Path(path).read_text(encoding='utf-8').splitlines()
    result=[];i=0
    while i<len(rows):
        if not rows[i].strip():
            i+=1;continue
        try:n=int(rows[i].strip())
        except Exception as exc:raise ValueError('INVALID_EXTXYZ_ATOM_COUNT') from exc
        if n<1:raise ValueError('EMPTY_EXTXYZ_FRAME')
        if i+1>=len(rows) or i+2+n>len(rows):raise ValueError('TRUNCATED_EXTXYZ_FRAME')
        kv=_keyvals(rows[i+1]);specs,width=_properties(kv.get('Properties','species:S:1:pos:R:3'))
        spec={s[0]:s for s in specs}
        if 'species' not in spec or 'pos' not in spec or spec['species'][2]!=1 or spec['pos'][2]!=3:
            raise ValueError('EXTXYZ_MISSING_SPECIES_OR_POSITIONS')
        numbers=[];positions=[];mask=[]
        for line in rows[i+2:i+2+n]:
            bits=line.split()
            if len(bits)!=width:raise ValueError('EXTXYZ_ATOM_COLUMNS_MISMATCH')
            sym=bits[spec['species'][3]]
            if sym not in ATOMIC:raise ValueError('UNKNOWN_ELEMENT')
            numbers.append(ATOMIC[sym]);positions.append([float(x) for x in bits[spec['pos'][3]:spec['pos'][3]+3]])
            if 'move_mask' in spec:
                m=spec['move_mask']
                if m[2]!=1 or m[1]!='L':raise ValueError('UNSUPPORTED_MOVE_MASK')
                mask.append(not _bool(bits[m[3]]))
        lattice=kv.get('Lattice')
        if lattice is None:cell=[[0.,0.,0.]]*3
        else:
            entries=list(map(float,lattice.split()))
            if len(entries)!=9:raise ValueError('INVALID_LATTICE')
            cell=[entries[j:j+3] for j in (0,3,6)]
        pbcs=kv.get('pbc','F F F').split()
        if len(pbcs)!=3:raise ValueError('INVALID_PBC')
        pbc=[_bool(v) for v in pbcs]
        if 'fixed_indices' in kv and 'move_mask' in spec:raise ValueError('CONFLICTING_CONSTRAINT_ENCODINGS')
        if 'move_mask' in spec:fixed=[j for j,locked in enumerate(mask) if locked];known=True
        elif 'fixed_indices' in kv:
            fixed=sorted(set(int(x) for x in kv['fixed_indices'].split(',') if x));known=True
        else:fixed=[];known=_bool(kv.get('constraints_known','F'))
        if 'constraints_known' in kv and not _bool(kv['constraints_known']) and known:raise ValueError('CONFLICTING_CONSTRAINT_KNOWN')
        geom={'numbers':numbers,'positions_angstrom':positions,'cell_angstrom':cell,'pbc':pbc,
              'fixed_indices':fixed,'constraints_known':known}
        validate_geometry(geom)
        frame={'submission_id':kv.get('submission_id',f'frame-{len(result)+1}'),
               'starting_minimum_id':kv.get('starting_minimum_id'), 'pes_id':kv.get('pes_id'),
               'parent_structure_id':kv.get('parent_structure_id'),'geometry':geom}
        result.append(frame)
        i+=2+n
    return result

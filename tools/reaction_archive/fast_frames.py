"""Source-file grouped, parity-checked, selective ASE ULM readers.

No mutation of native outputs. `.traj` reader backend is ASE's documented
TrajectoryReader.backend; a parity read at the first requested frame of *each*
file protects compatibility. If parity fails, the entire file is read through
ASE's original public interface, not through speculative reconstruction.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
from . import legacy_lean as old


def _mode_from_info(meta, path, frame):
    raw = old.search_dict(meta, 'lowest_eigenmode')
    if raw is None:
        raw = old.search_dict(meta, 'eigenmode')
    if raw is None:
        return {'status': 'MODE_ABSENT'}
    vec = np.asarray(raw)
    if vec.ndim != 2 or vec.shape[1] != 3 or not np.issubdtype(vec.dtype, np.number):
        return {'status': 'INVALID_MODE_SHAPE'}
    if not np.isfinite(vec).all():
        return {'status': 'INVALID_MODE_VALUES'}
    if not np.linalg.norm(vec) > 1e-14:
        return {'status': 'ZERO_MODE'}
    eig = old.search_dict(meta, 'lowest_eigenvalue')
    proj = old.search_dict(meta, 'order_projection')
    neg = old.search_dict(meta, 'negative_mode_count')
    tol = old.search_dict(meta, 'negative_eigenvalue_tolerance')
    return {'status': 'OK', 'vector': vec.astype(float).tolist(), 'shape': list(vec.shape),
            'lowest_eigenvalue_eV_A2': old.finite_number(eig),
            'negative_mode_count': int(neg) if neg is not None else None,
            'negative_eigenvalue_tolerance_eV_A2': old.finite_number(tol),
            'order_projection': str(proj) if proj is not None else None,
            'mode_geometry_parity': 'UNVERIFIED', 'source_path': path, 'source_frame': frame}


def _decode_fixed(constraints):
    from ase.io.jsonio import decode
    from ase.constraints import dict2constraint
    # Mirrors read_atoms() and original native_constraints() without building Atoms.
    ds = decode(constraints)
    types, fixed = [], []
    for d in ds:
        c = dict2constraint(d)
        typ = type(c).__name__
        if typ != 'FixAtoms':
            return {'constraints_known': False, 'fixed_indices': None,
                    'constraint_status': 'UNSUPPORTED_NATIVE_CONSTRAINT:' + typ}
        inds = sorted(int(i) for i in c.get_indices())
        fixed.extend(inds)
        types.append({'type': 'FixAtoms', 'indices': inds})
    return {'constraints_known': True, 'fixed_indices': sorted(set(fixed)),
            'constraint_status': 'NATIVE_ENDPOINT_TRAJECTORY',
            'constraint_descriptors': types}


def _native_backend(traj, frame):
    b = traj.backend[frame]
    if 'numbers' in b:
        numbers, pbc, constraints = b.numbers, b.pbc, b.get('constraints', '[]')
    else:
        numbers, pbc, constraints = traj.numbers, traj.pbc, traj.constraints
    geo = old.make_geometry(numbers, b.positions, b.cell, pbc)
    energy, source = None, 'NOT_STORED'
    if 'calculator' in b:
        calc = b.calculator
        for k in ('energy', 'free_energy'):
            if k in calc:
                e = old.finite_number(calc.get(k))
                if e is not None:
                    energy, source = e, 'serialized_calculator_' + k
                    break
    if energy is None and 'info' in b:
        info = b.get('info') or {}
        if isinstance(info, dict):
            for k in ('energy_eV', 'energy', 'total_energy_eV', 'potential_energy'):
                e = old.finite_number(info.get(k))
                if e is not None:
                    energy, source = e, 'native_info_' + k
                    break
    return {'status':'OK','geometry':geo,'energy_eV':energy,
            'energy_source':source, **_decode_fixed(constraints)}


def _read_from_reader(traj, path, kind, frame):
    if kind == 'endpoint':
        return _native_backend(traj, frame)
    b = traj.backend[frame]
    return _mode_from_info(b.get('info') or {}, path, frame)


def _full(path,kind,frame):
    if kind == 'endpoint':
        return old.read_native(path,frame)
    return old.read_recorded_mode(path,frame)


def _canon(o):
    # Strict representation parity: same values and statuses, not just shapes.
    return old.jsonline(o)


def process_path_group(args):
    """Function executed in child: open a file once, return (kind,frame,data).

    args = (absolute_path, [(kind,frame), ...], strict_ase)
    Parity across the first frame for each distinct kind in the file.
    """
    path, specs, strict_ase = args
    specs = sorted(set((str(kind),int(frame)) for kind,frame in specs))
    if not old.permitted_source(path):
        return (path, [(kind, fr, {'status':'SOURCE_PATH_ABSENT_OR_OUTSIDE_ALLOWED_ROOT'})
                       for kind,fr in specs], 'blocked')
    if not Path(path).is_file():
        return (path, [(kind, fr, {'status':'SOURCE_FILE_MISSING'}) for kind,fr in specs], 'missing')
    suffix = Path(path).suffix.lower()
    if suffix != '.traj':
        return (path, [(kind,fr,_full(path,kind,fr)) for kind,fr in specs], 'legacy_format')
    try:
        from ase.io.trajectory import Trajectory
        with Trajectory(path, mode='r') as traj:
            # Verify this real ASE version's backend produces exactly what its
            # public reader produces. On a mismatch use old reader for full path.
            fast = not strict_ase
            if fast:
                for kind in sorted(set(k for k,_ in specs)):
                    fr = next(fr for k,fr in specs if k==kind)
                    try:
                        a = _read_from_reader(traj,path,kind,fr)
                        b = _full(path,kind,fr)
                        if _canon(a) != _canon(b):
                            fast=False
                            break
                    except Exception:
                        fast=False
                        break
            result = []
            for kind,fr in specs:
                try:
                    # Full ASE on parity failure, still shared trajectory handle.
                    # Avoid fresh path opening in fallback by using traj[frame].
                    if fast:
                        item = _read_from_reader(traj,path,kind,fr)
                    elif kind == 'endpoint':
                        at = traj[fr]
                        geo=old.make_geometry(at.numbers,at.positions,at.cell,at.pbc)
                        energy,src=old.stored_energy(at)
                        item={'status':'OK','geometry':geo,'energy_eV':energy,
                              'energy_source':src,**old.native_constraints(at)}
                    else:
                        item=_mode_from_info(traj[fr].info,path,fr)
                except Exception as exc:
                    item={'status':('ENDPOINT' if kind=='endpoint' else 'MODE')+'_READ_FAILED:'+type(exc).__name__}
                result.append((kind,fr,item))
            return (path,result,'selective' if fast else 'ase_parity_fallback')
    except Exception as exc:
        # Keep errors explicit; do not fabricate data or call calculators.
        return (path, [(kind,fr,{'status':('ENDPOINT' if kind=='endpoint' else 'MODE')+'_READ_FAILED:'+type(exc).__name__}) for kind,fr in specs], 'failed:'+type(exc).__name__)

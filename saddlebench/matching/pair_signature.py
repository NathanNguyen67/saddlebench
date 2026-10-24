"""Conservative pair-distance signature rejection with persistent, source-bound cache.

This filter never credits a match. For identical cells/PBC and atom ordering,
maxdev(A,B) <= t implies that every corresponding periodic interatomic
shortest-distance differs by <= 2*t. When applicability cannot be certified,
return all possible candidates and leave the original matcher authoritative.
"""
from __future__ import annotations

from pathlib import Path
from math import gcd
import hashlib
import os
import tempfile
from zipfile import BadZipFile
import numpy as np
from saddlebench.matching import geometry as geom

SCHEMA = 'saddlebench-pair-distance-signature-v1'
GUARD = 1e-7


def pair_indices(n: int) -> np.ndarray:
    if n < 2:
        return np.empty((0, 2), dtype=np.int32)
    anchors = sorted({0, n // 3, 2*n // 3, n-1})
    step = max(1, n//3)
    while gcd(step, n) != 1:
        step += 1
    targets = [(i*step) % n for i in range(n)]
    return np.asarray([(anchor, j) for j in targets for anchor in anchors
                       if anchor != j], dtype=np.int32)


def geometry_key(g):
    try:
        pbc = tuple(g['pbc'])
        cell = np.asarray(g['cell_angstrom'], dtype=np.float64)
        pos = np.asarray(g['positions_angstrom'], dtype=np.float64)
        numbers = tuple(g['numbers'])
        if (len(pbc) != 3 or cell.shape != (3,3) or pos.shape != (len(numbers),3)
                or len(numbers)<2 or len(numbers)>1024 or not np.isfinite(cell).all()
                or not np.isfinite(pos).all()):
            return None
        return (numbers, pbc, tuple(cell.reshape(-1)))
    except (TypeError, ValueError, KeyError, IndexError, OverflowError):
        return None


def one_signature(g, indices):
    pos = np.asarray(g['positions_angstrom'], dtype=np.float64)
    d = pos[indices[:, 0]]-pos[indices[:, 1]]
    # Use the original SaddleBench MIC, not a new approximation.
    corrected = geom._mic(d, np.asarray(g['cell_angstrom'], dtype=np.float64), g['pbc'])
    result = np.linalg.norm(corrected,axis=1)
    if not np.isfinite(result).all():
        raise ValueError('INVALID_SIGNATURE')
    return result


class PersistentPairSignatures:
    """Per-catalog persistent cache. Never rely on unauthenticated cache contents.

    Source identity is computed by the existing group-index machinery from the
    ordered, complete reference geometries and IDs; no topology or model hints.
    Disk cache lives in caller-controlled index-cache directory. A partial write
    is never exposed as a valid cache; source/hash/schema checks are mandatory.
    """

    def __init__(self, geoms, source_sha256: str, cache_root: Path | str):
        self.geoms = geoms
        self.keys = [geometry_key(g) for g in geoms]
        self.indices = pair_indices(len(geoms[0]['numbers'])) if geoms else pair_indices(0)
        self.active = np.zeros(len(geoms), dtype=bool)
        self.data = np.zeros((len(geoms), len(self.indices)), dtype=np.float64)
        self.state = 'DISABLED'
        self.source_sha256 = source_sha256
        self.cache_root = Path(cache_root).expanduser().absolute()
        if not (len(source_sha256) == 64 and all(c in '0123456789abcdef' for c in source_sha256)):
            raise ValueError('INVALID_SOURCE_IDENTITY')
        self.cache_file = self.cache_root/('signatures_'+source_sha256+'.npz')
        self._load_or_build()

    def _load_or_build(self):
        root = self.cache_root
        # No cache path symlinks: existing persistent state cannot redirect our writes.
        for part in (root,*root.parents):
            if part.is_symlink():
                raise ValueError('SIGNATURE_CACHE_SYMLINK_PATH')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.cache_file.is_symlink():
            raise ValueError('SIGNATURE_CACHE_SYMLINK_FILE')
        if self.cache_file.is_file() and self._load():
            self.state='LOADED'
            return
        # A bad cache fails open to recomputation, never to unsafe rejection.
        for i,(key,g) in enumerate(zip(self.keys,self.geoms)):
            if key is None or len(key[0]) != len(self.geoms[0]['numbers']):
                continue
            try:
                sig=one_signature(g,self.indices)
                if sig.shape == (len(self.indices),):
                    self.data[i] = sig
                    self.active[i] = True
            except (ValueError,KeyError,TypeError,IndexError,OverflowError,
                    np.linalg.LinAlgError,FloatingPointError):
                # Leave this reference unscreened; original matcher decides.
                pass
        self.state='BUILT'
        h=hashlib.sha256(self.data.tobytes()+self.active.tobytes()).hexdigest()
        fd,tmp=tempfile.mkstemp(prefix='.pending_signature_',suffix='.npz',dir=root)
        try:
            with os.fdopen(fd,'wb') as f:
                np.savez(f,signature=self.data,active=self.active,
                         source_sha=np.asarray(self.source_sha256),
                         digest=np.asarray(h),schema=np.asarray(SCHEMA))
                f.flush();os.fsync(f.fileno())
            try:
                os.link(tmp,self.cache_file)  # never overwrite immutable caches
            except FileExistsError:
                # Other process won race. Existing cache will be checked next run.
                self.state='RACE_BUILT'
        finally:
            if os.path.exists(tmp):os.unlink(tmp)

    def _load(self):
        try:
            with np.load(self.cache_file,allow_pickle=False) as z:
                if set(z.files) != {'signature','active','source_sha','digest','schema'}:
                    return False
                if str(z['schema']) != SCHEMA or str(z['source_sha']) != self.source_sha256:
                    return False
                data=z['signature'];active=z['active'];expected=str(z['digest'])
            if (data.dtype != np.float64 or active.dtype != np.bool_ or
                data.shape != self.data.shape or active.shape != self.active.shape or
                not np.isfinite(data).all() or not np.all(data >= 0)):
                return False
            if hashlib.sha256(data.tobytes()+active.tobytes()).hexdigest()!=expected:
                return False
            self.data=data;self.active=active
            return True
        except (OSError,ValueError,KeyError,TypeError,EOFError,RuntimeError,BadZipFile):
            return False

    def possible(self,query,threshold):
        """True means undecided; false means proved impossible. No false accepts."""
        keep=np.ones(len(self.geoms),dtype=bool)
        qkey=geometry_key(query)
        if qkey is None or not self.indices.size:
            return keep
        compatible=np.flatnonzero(self.active & np.fromiter((k == qkey for k in self.keys),dtype=bool,count=len(self.keys)))
        if not len(compatible):
            return keep
        try:
            qsig=one_signature(query,self.indices)
            bound=2*float(threshold)+GUARD
            for end in (8,64,256,self.data.shape[1]):
                now=compatible[keep[compatible]]
                if not len(now):break
                n=min(end,self.data.shape[1])
                # Check just the *new* block; avoid repeatedly comparing prior ones.
                left=max(0,8 if end==64 else 64 if end==256 else 256 if end==self.data.shape[1] else 0)
                if n<=left:continue
                rejected=np.any(np.abs(self.data[now,left:n]-qsig[None,left:n]) > bound,axis=1)
                keep[now[rejected]]=False
            return keep
        except (ValueError,TypeError,IndexError,OverflowError,
                np.linalg.LinAlgError,FloatingPointError):
            return np.ones(len(self.geoms),dtype=bool)

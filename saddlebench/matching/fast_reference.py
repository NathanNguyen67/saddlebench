"""Content-pinned source filtering for first-hit non-FO matching.

This is *not* the full official score: other diagnostics are deliberately absent.
No changes to the pinned reaction matching threshold or candidate identity rules.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import gzip,hashlib,json,os,tempfile,time
from pathlib import Path
import numpy as np

NONFO_KIND='KNOWN_NON_FIRST_ORDER'
FAST_SCHEMA='saddlebench-experimental-fast-nonfo-v1'

# The authoritative Hugging Face snapshot downloader intentionally creates
# snapshot-file symlinks to a content-addressed blobs/ store.  The v1 test-only
# fixture was a regular file, so blanket symlink rejection incorrectly blocked
# the actual previously verified published reference.
# Dataset adapters may supply any checksum-pinned Hugging Face dataset cache.


def _trusted_diagnostic_file(private):
    source=Path(private)/'diagnostic_saddles.jsonl.gz'
    if not source.is_file():
        raise ValueError('DIAGNOSTIC_SOURCE_NOT_PRESENT: '+str(source))
    if source.is_symlink():
        # The filename must come from the pinned Hugging Face snapshot.
        # Cache storage may be symlinked, relocated, or materialized outside
        # the repository's blobs/ directory. Physical target location is not
        # an authoritative content identity: published SHA256 is.
        parents=list(source.parents)
        repos=[p for p in parents if p.name.startswith('datasets--')]
        if len(repos)!=1:
            raise ValueError('DIAGNOSTIC_SYMLINK_NOT_IN_PINNED_HF_CACHE: '+str(source))
        repo=repos[0]
        snapshot=source.parent
        if (snapshot.parent.name!='snapshots' or snapshot.parent.parent!=repo
                or not snapshot.name or '/' in snapshot.name):
            raise ValueError('DIAGNOSTIC_SYMLINK_NOT_IN_HF_SNAPSHOT: '+str(source))
        # Do not use Path.resolve() to constrain a valid cached data file to
        # a particular physical storage layout. File type was checked above;
        # published digest is checked by verify_published_diagnostics() and
        # again by make_nonfo_cache() before decoding records.
    return source


def verify_published_diagnostics(private,public_cases):
    """Validate diagnostic bytes against any self-described pinned reference."""
    from saddlebench.score_modes import verify_reference
    root=Path(private)
    source=_trusted_diagnostic_file(root)
    verify_reference(root,public_cases)
    checksums=json.loads((root/'CHECKSUMS.json').read_text())
    if checksums.get('diagnostic_saddles.jsonl.gz') != digest_file(source):
        raise ValueError('PUBLISHED_DIAGNOSTIC_CHECKSUM_MISMATCH')
    return {'diagnostic_source':str(source),'diagnostic_sha256':digest_file(source),
            'diagnostic_is_hf_snapshot_link':source.is_symlink(),
            'published_checksum_count':len(checksums)}


def digest_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()


def make_nonfo_cache(private,cache_root):
    """One-time subset extraction from frozen diagnostic gzip; never modify producer input.

    Lazy JSON decoding for the common non-NONFO case avoids expensive object creation
    but still performs the gzip scan when preparing a new source snapshot.
    """
    private=Path(private)
    source=_trusted_diagnostic_file(private)
    sha=digest_file(source)
    # The source may be a relocated snapshot symlink. Refuse to decode unless
    # the actual contents match the *published*, pinned diagnostic SHA256.
    # This is repeated even if the caller already ran verify_published_diagnostics:
    # make_nonfo_cache must not accidentally become a trust bypass.
    checksums=json.loads((private/'CHECKSUMS.json').read_text())
    expected=checksums.get('diagnostic_saddles.jsonl.gz')
    if not isinstance(expected,str) or len(expected)!=64 or sha!=expected:
        raise ValueError('NONFO_SOURCE_PUBLISHED_SHA256_MISMATCH')
    root=Path(cache_root);root.mkdir(parents=True,exist_ok=True)
    record=root/('nonfo_'+sha+'.jsonl')
    manifest=root/('nonfo_'+sha+'.manifest.json')
    if record.exists() and manifest.exists():
        meta=json.loads(manifest.read_text())
        if meta.get('schema')!='saddlebench-fast-nonfo-cache-v1' or meta.get('input_sha256')!=sha or meta.get('subset_sha256')!=digest_file(record):
            raise ValueError('NONFO_CACHE_INVALID')
        return record,meta,'CACHED'
    if record.exists() or manifest.exists():raise ValueError('INCOMPLETE_CACHE_REQUIRES_MANUAL_INSPECTION')
    t0=time.perf_counter()
    fd,tmp=tempfile.mkstemp(prefix='.pending_nonfo_',dir=root)
    scanned=selected=0
    try:
        with os.fdopen(fd,'wb') as writer,gzip.open(source,'rb') as reader:
            for line in reader:
                scanned+=1
                # This substring is only an optimization: JSON verifies the field exactly.
                if b'KNOWN_NON_FIRST_ORDER' not in line:continue
                item=json.loads(line)
                if item.get('diagnostic_kind')!=NONFO_KIND:continue
                writer.write(json.dumps(item,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n')
                selected+=1
            writer.flush();os.fsync(writer.fileno())
        os.link(tmp,record)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    meta={'schema':'saddlebench-fast-nonfo-cache-v1','input_sha256':sha,
          'subset_sha256':digest_file(record),'source_rows':scanned,'nonfo_rows':selected,
          'prepare_seconds':time.perf_counter()-t0,
          'note':'KNOWN_NON_FIRST_ORDER records only; no other diagnostic penalties covered'}
    data=json.dumps(meta,sort_keys=True,indent=2).encode()+b'\n'
    fd,tmp=tempfile.mkstemp(prefix='.pending_nonfo_manifest_',dir=root)
    try:
        with os.fdopen(fd,'wb') as writer:writer.write(data);writer.flush();os.fsync(writer.fileno())
        os.link(tmp,manifest)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return record,meta,'BUILT'


def load_nonfo_for_case(index_file,case,require_starting_minimum=False):
    """Structure-scoped diagnostic evidence; optional historical start filter."""
    selected=[]
    with open(index_file,'r',encoding='utf8') as reader:
        for line in reader:
            item=json.loads(line)
            if item.get('diagnostic_kind')!=NONFO_KIND:raise ValueError('CACHE_KIND_INVALID')
            if item['structure_id']!=case['structure_id']:continue
            starts=item.get('starting_minimum_ids') or []
            if require_starting_minimum and starts and case.get('starting_minimum_id') not in starts:continue
            item['start_known']=bool(starts)
            selected.append(item)
    return selected



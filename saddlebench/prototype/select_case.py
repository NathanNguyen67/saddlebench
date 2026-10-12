"""Select a SaddleBench starting minimum from cases.csv without installing ASE."""
from __future__ import annotations
import argparse,csv,pathlib,shutil

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case-id',required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--cases',default=None)
    args=p.parse_args(argv)
    folder=pathlib.Path(__file__).parent
    index=pathlib.Path(args.cases) if args.cases else folder/'cases.csv'
    with index.open(newline='',encoding='utf8') as f:
        found=[r for r in csv.DictReader(f) if r['case_id']==args.case_id]
    if len(found)!=1:raise ValueError('CASE_ID_NOT_FOUND_OR_DUPLICATE')
    rel=pathlib.PurePosixPath(found[0]['minimum_traj'])
    if rel.is_absolute() or '..' in rel.parts or rel.suffix!='.traj':raise ValueError('INVALID_CASE_TRAJ_PATH')
    source=index.parent.joinpath(*rel.parts)
    if source.is_symlink() or not source.is_file():raise ValueError('CASE_TRAJ_MISSING_OR_SYMLINK')
    target=pathlib.Path(args.out)
    with target.open('xb') as dst, source.open('rb') as src:shutil.copyfileobj(src,dst)
    print(str(target))

if __name__=='__main__':main()

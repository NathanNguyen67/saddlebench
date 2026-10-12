# Submission format (prototype)

For one case: a valid ASE `.traj` containing one frame per proposed saddle.

- Each frame must have identical ordered atomic numbers, compatible PBC and
  cell, and consistent fixed-atom constraints with the chosen starting minimum.
- The scorer uses core-drift maxdev <=0.2 A; no topology-based matching.
- The same `.traj` may have repeated geometries; repeated credit is never granted.
- Record the actual walltime in seconds, including attempts that did not yield a
  unique credited reaction. Also record calculator/PES and hardware separately.
- Eligible result needs 0.005 eV/A saddle, both DoubleMin endpoints 0.0025 eV/A
  in the frozen reference, connected and barrier <1.5 eV; these are source
  reference filters, not re-optimization of user submitted structures.

For multiple cases, create `submission.csv`:

```csv
case_id,trajectory,elapsed_seconds
case0001,my_case0001.traj,3600
case0002,my_case0002.traj,4800
```

Relative trajectory paths are relative to the CSV's directory.

Do NOT include reference geometries in GitHub issues, PRs or public benchmark
submissions. An authenticated local grader can provide detailed feedback, but
that does not make the private reference public.

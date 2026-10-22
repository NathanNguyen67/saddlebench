"""Unit preflight tests only: no Slurm job, no LS6 source, no GPU."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/"run_direct_vm_small.sh"


def shim(root, command, data):
    path = root / command
    path.write_text("#!/bin/sh\n" + data + "\n")
    path.chmod(0o755)


class DirectPreflight(unittest.TestCase):
    def run_case(self, hostname, partition, *, workers="4", bad_pointer=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"source.sqlite"
            source.write_bytes(b"immutable fixture")
            pointer=root/"pointer.txt"
            pointer.write_text(str(source) + "\n")
            shim(root, "hostname", "echo " + hostname)
            shim(root, "squeue", "echo " + partition)
            shim(root, "nproc", "echo 16")
            env=os.environ.copy()
            env["PATH"] = str(root) + os.pathsep + env["PATH"]
            env["SBE_REGISTRY_POINTER"] = str(root/"missing.txt") if bad_pointer else str(pointer)
            env["SBE_OUTPUT_BASE"] = str(root/"output")
            env["SBE_WORKERS"] = workers
            env.pop("SLURM_JOB_PARTITION", None)
            env.pop("SLURM_JOB_ID", None)
            out=subprocess.run(["bash",str(SCRIPT),"--preflight-only"],env=env,
                               capture_output=True,text=True,timeout=20)
            self.assertFalse((root/"output").exists())
            return out

    def test_openmp_one_does_not_fake_single_cpu_allocation(self):
        # Reproduce LS6: taskset -pc $$ -> 0-15; nproc -> 1 because
        # OpenMP sets its process-thread limit, not the job's CPU entitlement.
        # The launcher should remove only these limits for the nproc probe.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"source.sqlite"
            source.write_bytes(b"immutable fixture")
            pointer=root/"pointer.txt"
            pointer.write_text(str(source) + "\n")
            shim(root, "hostname", "echo v320-004")
            shim(root, "squeue", "echo vm-small")
            shim(root, "nproc", 'if [ "${OMP_NUM_THREADS:-}" = 1 ] || [ "${OMP_THREAD_LIMIT:-}" = 1 ]; then echo 1; else echo 16; fi')
            env=os.environ.copy()
            env.update({"PATH":str(root)+os.pathsep+env["PATH"],
                        "OMP_NUM_THREADS":"1", "OMP_THREAD_LIMIT":"1",
                        "SBE_REGISTRY_POINTER":str(pointer),
                        "SBE_OUTPUT_BASE":str(root/"output"),
                        "SBE_WORKERS":"4"})
            env.pop("SLURM_JOB_PARTITION",None)
            env.pop("SLURM_JOB_ID",None)
            p=subprocess.run(["bash",str(SCRIPT),"--preflight-only"],env=env,
                             capture_output=True,text=True,timeout=20)
            self.assertEqual(p.returncode,0,(p.stdout,p.stderr))
            self.assertIn("DIRECT_VM_SMALL_PREFLIGHT=PASS",p.stdout)
            self.assertFalse((root/"output").exists())

    def test_real_single_cpu_affinity_still_blocks_four_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"source.sqlite"
            source.write_bytes(b"immutable fixture")
            pointer=root/"pointer.txt"
            pointer.write_text(str(source) + "\n")
            shim(root, "hostname", "echo v320-004")
            shim(root, "squeue", "echo vm-small")
            shim(root, "nproc", "echo 1")
            env=os.environ.copy()
            env.update({"PATH":str(root)+os.pathsep+env["PATH"],
                        "SBE_REGISTRY_POINTER":str(pointer),
                        "SBE_OUTPUT_BASE":str(root/"output"),
                        "SBE_WORKERS":"4"})
            env.pop("SLURM_JOB_PARTITION",None)
            env.pop("SLURM_JOB_ID",None)
            p=subprocess.run(["bash",str(SCRIPT),"--preflight-only"],env=env,
                             capture_output=True,text=True,timeout=20)
            self.assertNotEqual(p.returncode,0)
            self.assertIn("available CPUs=1 below requested workers=4",p.stderr)
            self.assertFalse((root/"output").exists())

    def test_verified_ssh_allocation(self):
        p=self.run_case("v320-004", "vm-small")
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn("DIRECT_VM_SMALL_PREFLIGHT=PASS",p.stdout)
        self.assertIn("WORKERS=4",p.stdout)
        self.assertIn("ALLOCATION_VERIFIED_BY=SLURM_SQUEUE_RUNNING_ALLOCATION",p.stdout)

    def test_denies_login_node(self):
        p=self.run_case("login1", "vm-small")
        self.assertNotEqual(p.returncode,0)
        self.assertIn("REFUSE_LOGIN_NODE",p.stderr)

    def test_denies_unverified_allocation(self):
        p=self.run_case("v320-004", "gpu-a100")
        self.assertNotEqual(p.returncode,0)
        self.assertIn("no running vm-small allocation",p.stderr)

    def test_rejects_invalid_workers(self):
        p=self.run_case("v320-004", "vm-small", workers="17")
        self.assertNotEqual(p.returncode,0)
        self.assertIn("SBE_WORKERS",p.stderr)

    def test_refuses_missing_registry_pointer(self):
        p=self.run_case("v320-004", "vm-small", bad_pointer=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn("missing/untrusted published registry pointer",p.stderr)

if __name__ == "__main__":
    unittest.main()

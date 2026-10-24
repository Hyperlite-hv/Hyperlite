"""The lab's annotations (lab/annotate.py): after a green run, a notice says which tests passed and which were skipped,
and why, so a green job is not read as more than what ran."""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("annotate", ROOT / "lab" / "annotate.py")
annotate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(annotate)

LOG = """lab/tests/test_hyperlite_cluster.py ....s.
=========================== short test summary info ============================
PASSED lab/tests/test_hyperlite_cluster.py::test_three_nodes_form_a_cluster
PASSED lab/tests/test_hyperlite_cluster.py::test_a_network_on_every_node
SKIPPED [1] lab/tests/test_hyperlite_cluster.py:263: no nested virtualization on this host: the VMs of the lab cannot run a VM of their own
================== 2 passed, 1 skipped in 812.40s (0:13:32) ===================
"""


def test_a_green_run_says_what_passed_and_what_was_skipped_and_why(tmp_path, capsys):
    (tmp_path / "pytest.log").write_text(LOG)
    annotate.summary(tmp_path)
    out = capsys.readouterr().out
    assert out.startswith("::notice title=pytest summary::2 passed, 1 skipped in 812.40s (0:13:32)%0A")
    assert "PASSED lab/tests/test_hyperlite_cluster.py::test_a_network_on_every_node" in out
    assert "no nested virtualization on this host" in out
    assert out.count("\n") == 1  # one annotation: the checks' API returns it whole


def test_a_missing_log_is_said(tmp_path, capsys):
    annotate.summary(tmp_path)
    assert capsys.readouterr().out.startswith("::warning title=pytest summary::")

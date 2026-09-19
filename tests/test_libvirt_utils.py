"""libvirt helper functions that do not need a running hypervisor."""

from app.core.libvirt_utils import get_vm_uptime_s


def test_uptime_is_unknown_for_a_vm_without_a_pid_file():
    assert get_vm_uptime_s("vm-that-does-not-exist") is None

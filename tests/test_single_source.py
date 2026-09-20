"""The APT repository location must be defined in one place only."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / "installer" / "apt-source.conf"
ADDRESS = re.compile(r"github\.io|https?://\d{1,3}(?:\.\d{1,3}){3}:\d+|releases/download/")


def _sources():
    files = subprocess.run(
        ["git", "ls-files", "installer", "scripts", "app"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [ROOT / f for f in files if not f.endswith((".png", ".gz", ".asc"))]


def test_the_canonical_file_defines_the_address():
    text = CONF.read_text()
    assert re.search(r'^HYPERLITE_APT_URL="https://[^"]+"$', text, re.M)


def test_no_script_or_module_hard_codes_a_repository_address():
    offenders = []
    for path in _sources():
        if path == CONF or path == Path(__file__):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if ADDRESS.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")
    assert not offenders, "repository address hard-coded outside installer/apt-source.conf:\n" + "\n".join(offenders)


def test_the_mirror_check_reads_the_canonical_address():
    result = subprocess.run(
        ["bash", "-c", f'. "{CONF}"; echo "$HYPERLITE_APT_URL"'], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip().startswith("https://")


def test_the_documentation_links_to_the_canonical_iso_address():
    iso_url = re.search(r'^HYPERLITE_ISO_URL="([^"]+)"$', CONF.read_text(), re.M).group(1)
    for name in ("README.md", "docs/deployment.md"):
        text = (ROOT / name).read_text()
        links = re.findall(r"https://github\.com/[^\s)`]*/releases/download/[^\s)`]*", text)
        assert links or name != "README.md", "README.md must link to the ISO"
        assert all(link == iso_url for link in links), f"{name} links to an ISO address other than {iso_url}"

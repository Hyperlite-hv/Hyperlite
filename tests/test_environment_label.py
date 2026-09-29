"""The installation label (HYPERLITE_ENV_LABEL) that marks a non-production instance in the dashboard."""

from app import main


def test_no_label_by_default(monkeypatch):
    monkeypatch.delenv("HYPERLITE_ENV_LABEL", raising=False)
    assert main._environment_label() is None
    monkeypatch.setenv("HYPERLITE_ENV_LABEL", "   ")
    assert main._environment_label() is None


def test_label_is_trimmed_and_bounded(monkeypatch):
    monkeypatch.setenv("HYPERLITE_ENV_LABEL", "  DEV ")
    assert main._environment_label() == "DEV"
    monkeypatch.setenv("HYPERLITE_ENV_LABEL", "x" * 100)
    assert len(main._environment_label()) == 24

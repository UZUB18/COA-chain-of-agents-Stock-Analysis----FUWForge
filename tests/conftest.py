"""Shared offline evidence used by the AMD acceptance tests."""

import gzip
import shutil
from pathlib import Path

import pytest

_PROVIDER_KEY_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENCODE_GO_API_KEY",
    "TYPESAFE_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_real_credentials(request, monkeypatch, tmp_path_factory):
    """Keep offline tests hermetic: never read the developer's real .env or keys.

    Without this, a checkout that has a populated .env lets code paths that
    construct a provider backend on their own (e.g. targeted search) make paid
    live calls during the offline suite. Tests marked ``live`` keep real access.
    """
    if request.node.get_closest_marker("live"):
        return
    for name in _PROVIDER_KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    missing = tmp_path_factory.getbasetemp() / "no-such.env"
    monkeypatch.setenv("FUWFORGE_ENV_PATH", str(missing))
    import src.agents.model as model
    import src.evidence as evidence
    import src.sentry as sentry

    monkeypatch.setattr(model, "_GEMINI_ENV_PATH", missing)
    monkeypatch.setattr(evidence, "_ENV_PATH", missing)
    monkeypatch.setattr(sentry, "_ENV_PATH", missing)


@pytest.fixture(scope="session")
def amd_evidence_dir(tmp_path_factory):
    from src.evidence import _document_text

    source = Path(__file__).parent / "fixtures" / "amd"
    destination = tmp_path_factory.mktemp("amd-evidence")
    for archive, name in (
        ("sec_companyfacts.json.gz", "sec_companyfacts.json"),
        ("filing.html.gz", "D002.bytes"),
    ):
        (destination / name).write_bytes(gzip.decompress((source / archive).read_bytes()))
    (destination / "D002.txt").write_text(
        _document_text((destination / "D002.bytes").read_bytes(), "text/html"),
        encoding="utf-8",
    )
    shutil.copyfile(source / "evidence.json", destination / "evidence.json")
    return destination

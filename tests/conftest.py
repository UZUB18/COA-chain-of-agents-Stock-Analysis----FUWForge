"""Shared offline evidence used by the AMD acceptance tests."""

import gzip
import shutil
from pathlib import Path

import pytest


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

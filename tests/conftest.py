import pytest

from gh_workflow_fix.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(
        gh_token="t",
        gh_repo="acme/app",
        gh_oc_auto=True,
        webhook_secret="s3cret",
        data_dir=tmp_path,
        opencode_bin=tmp_path / "opencode",
        gh_api_base="https://api.example.com",
    )

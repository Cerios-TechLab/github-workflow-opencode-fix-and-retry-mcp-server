from pathlib import Path

from gh_workflow_fix.config import load_config


def test_load_config_defaults():
    cfg = load_config(
        {
            "GH_TOKEN": "tok",
            "GH_REPO": "acme/app",
            "GH_OC_AUTO": "true",
            "WEBHOOK_SECRET": "s3cret",
        }
    )
    assert cfg.gh_repo == "acme/app"
    assert cfg.gh_oc_auto is True
    assert cfg.self_heal_marker == "# self-heal: true"
    assert cfg.retry_delays_min == (15, 30, 60)
    assert cfg.webhook_port == 18080
    assert cfg.opencode_model is None


def test_load_config_overrides():
    cfg = load_config(
        {
            "GH_TOKEN": "tok",
            "GH_REPO": "acme/app",
            "GH_OC_AUTO": "false",
            "WEBHOOK_SECRET": "s3cret",
            "RETRY_DELAYS_MIN": "5,10",
            "DATA_DIR": "/tmp/data",
            "WEBHOOK_PORT": "9999",
            "OPENCODE_BIN": "/tmp/opencode",
        }
    )
    assert cfg.gh_oc_auto is False
    assert cfg.retry_delays_min == (5, 10)
    assert cfg.data_dir == Path("/tmp/data")
    assert cfg.webhook_port == 9999
    assert cfg.opencode_bin == Path("/tmp/opencode")
    assert cfg.opencode_model is None


def test_load_config_opencode_model():
    cfg = load_config(
        {
            "GH_TOKEN": "tok",
            "GH_REPO": "acme/app",
            "GH_OC_AUTO": "true",
            "WEBHOOK_SECRET": "s3cret",
            "OPENCODE_MODEL": "opencode/big-pickle",
        }
    )
    assert cfg.opencode_model == "opencode/big-pickle"


def test_missing_required_env_raises():
    try:
        load_config({})
    except ValueError as exc:
        assert "GH_TOKEN" in str(exc)
    else:
        raise AssertionError("expected ValueError")

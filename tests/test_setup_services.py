import json
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, select

from app.database import Base
from app.models import UsageLog


def _module():
    path = Path(__file__).parents[1] / "scripts" / "setup_services.py"
    spec = importlib.util.spec_from_file_location("setup_services_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _migration_module():
    path = Path(__file__).parents[1] / "scripts" / "migrate_sqlite_to_postgres.py"
    spec = importlib.util.spec_from_file_location("migration_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bootstrap_routes_include_deepseek_only_when_configured():
    module = _module()
    without = json.loads(module.bootstrap_routes(deepseek_key=""))
    with_key = json.loads(module.bootstrap_routes(deepseek_key="deepseek-secret"))

    assert not any(route["name"] == "deepseek-flash" for route in without)
    deepseek = next(route for route in with_key if route["name"] == "deepseek-flash")
    assert deepseek["upstream_headers"]["Authorization"] == "Bearer deepseek-secret"


def test_setup_env_write_preserves_comments_and_permissions(tmp_path):
    module = _module()
    env_file = tmp_path / ".env.stack"
    env_file.write_text("# keep this\nMODEL_ROUTER_PORT=4000\n")

    module.write_env(env_file, {"MODEL_ROUTER_PORT": "5000", "NEW_VALUE": "ok"})

    assert module.parse_env(env_file) == {"MODEL_ROUTER_PORT": "5000", "NEW_VALUE": "ok"}
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_generated_fernet_key_has_expected_shape():
    assert len(_module().generate_fernet_key()) == 44


def test_placeholder_values_are_not_treated_as_credentials():
    module = _module()

    assert module.is_placeholder("replace-with-generated-key")
    assert module.is_placeholder("mrk_replace_with_key")
    assert not module.is_placeholder("mrk_12345678_real-value")


def test_migration_nulls_missing_nullable_foreign_keys():
    module = _migration_module()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        row = {"model_route_id": "deleted-route"}
        normalized, adjustments = module.normalize_foreign_keys(row, UsageLog.__table__, connection)

    assert normalized["model_route_id"] is None
    assert adjustments == ["usage_logs.model_route_id"]

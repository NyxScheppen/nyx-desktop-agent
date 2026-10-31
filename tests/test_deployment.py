from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_compose_defines_persistent_postgres_and_nyx_services() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())

    assert set(compose["services"]) == {"postgres", "nyx"}
    assert compose["services"]["postgres"]["volumes"] == [
        "postgres_data:/var/lib/postgresql/data"
    ]
    nyx = compose["services"]["nyx"]
    assert nyx["environment"]["NYX_DATABASE_URL"].startswith(
        "postgresql://${POSTGRES_USER:-nyx}:"
    )
    assert nyx["volumes"] == ["nyx_data:/app/data"]
    assert (
        "${NYX_AUTH_TOKEN:?Set NYX_AUTH_TOKEN in .env}"
        in nyx["environment"].values()
    )


def test_dockerfile_uses_locked_runtime_and_empty_cloud_config() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "NYX_CONFIG=/dev/null" in dockerfile
    assert "USER nyx" in dockerfile
    assert "ln -s /app/data/workspace /app/workspace" in dockerfile
    assert 'CMD ["/app/.venv/bin/python", "-m", "nyx.main"]' in dockerfile

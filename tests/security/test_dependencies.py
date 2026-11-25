import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
FORBIDDEN = {"python-jose", "passlib", "pycrypto", "pyjwt-jose", "jose", "pickle5", "pyyaml"}
EXPECTED_RUNTIME = {
    "argon2-cffi",
    "cryptography",
    "fastapi",
    "httpx",
    "psycopg",
    "pydantic",
    "pydantic-settings",
    "pyjwt",
    "slowapi",
    "sqlalchemy",
    "uvicorn",
}


def _lock() -> dict[str, Any]:
    return tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))


def test_forbidden_dependencies_absent() -> None:
    packages = {package["name"] for package in _lock()["package"]}

    assert packages.isdisjoint(FORBIDDEN)


def test_runtime_dependencies_are_the_documented_stack() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {
        entry.split("[")[0].split(">")[0].split("=")[0].strip().lower()
        for entry in project["dependencies"]
    }

    assert names == EXPECTED_RUNTIME


def test_every_locked_artifact_has_a_hash() -> None:
    for package in _lock()["package"]:
        if package.get("source", {}).get("editable") or package.get("source", {}).get("virtual"):
            continue
        artifacts = [package.get("sdist"), *package.get("wheels", [])]
        artifacts = [artifact for artifact in artifacts if artifact]
        assert artifacts, package["name"]
        assert all(artifact["hash"].startswith("sha256:") for artifact in artifacts), package[
            "name"
        ]

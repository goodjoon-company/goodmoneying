import shutil
import subprocess
import sys
from pathlib import Path


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _repository_build_artifacts() -> set[Path]:
    candidates = [Path("dist"), Path("build")]
    candidates.extend(Path("apps/upbit_gateway").glob("*.egg-info"))
    return {path for path in candidates if path.exists()}


def test_built_wheel_loads_packaged_catalog_from_a_clean_venv_and_temporary_cwd(
    tmp_path: Path,
) -> None:
    build_root = tmp_path / "project"
    build_root.mkdir()
    shutil.copy2("pyproject.toml", build_root / "pyproject.toml")
    shutil.copytree(
        "apps/upbit_gateway",
        build_root / "apps/upbit_gateway",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
    )
    distribution_dir = tmp_path / "wheelhouse"
    artifacts_before = _repository_build_artifacts()

    build = _run(
        ["uv", "build", "--wheel", "--out-dir", str(distribution_dir)],
        cwd=build_root,
    )
    assert build.returncode == 0, build.stderr
    wheel = next(distribution_dir.glob("*.whl"))

    venv = tmp_path / "venv"
    create_venv = _run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
    )
    assert create_venv.returncode == 0, create_venv.stderr
    python = venv / "bin/python"
    install = _run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        cwd=tmp_path,
    )
    assert install.returncode == 0, install.stderr

    isolated_cwd = tmp_path / "isolated-cwd"
    isolated_cwd.mkdir()
    smoke = _run(
        [
            str(python),
            "-c",
            (
                "from goodmoneying_upbit_gateway.catalog import load_catalog; "
                "catalog = load_catalog(); "
                "assert catalog['catalog_version'] == '1.6.3'; "
                "assert len(catalog['rest_endpoints']) == 51"
            ),
        ],
        cwd=isolated_cwd,
    )

    assert smoke.returncode == 0, smoke.stderr
    assert _repository_build_artifacts() == artifacts_before

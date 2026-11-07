"""Build and start the whole stack in Docker, run the end-to-end tests against it, tear it down.

    python e2e/run.py            # up, test, down (volumes included)
    python e2e/run.py --keep     # leave the stack running afterwards, to poke at it
    python e2e/run.py -k journey # extra arguments go to pytest

It uses its own compose project and ports (3000 -> 13000, 8001 -> 18001, 5432 -> 15432), so it
never touches your development stack or its data. Needs Docker, and `pip install -r
backend/requirements-dev.txt` for pytest and httpx.
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "modelhub-e2e"
PORTS = {"DB_PORT": "15432", "API_PORT": "18001", "WEB_PORT": "13000"}
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "-f", str(ROOT / "e2e" / "docker-compose.e2e.yml")]


def main() -> int:
    args = sys.argv[1:]
    keep = "--keep" in args
    pytest_args = [a for a in args if a != "--keep"]
    env = {**os.environ, "COMPOSE_PROJECT_NAME": PROJECT, **PORTS}
    for name in ("JWT_SECRET", "ENCRYPTION_KEY", "MODELHUB_ENV", "CORS_ORIGINS", "NEXT_PUBLIC_API_BASE"):
        env.pop(name, None)  # the run must not depend on (or leak) the caller's own settings

    def compose(*a, check=True):
        return subprocess.run([*COMPOSE, *a], cwd=ROOT, env=env, check=check)

    try:
        compose("down", "-v", "--remove-orphans", check=False)  # start from nothing
        compose("up", "-d", "--build", "--wait")
        test_env = {
            **env,
            "E2E_API": f"http://127.0.0.1:{PORTS['API_PORT']}",
            "E2E_WEB": f"http://127.0.0.1:{PORTS['WEB_PORT']}",
            "E2E_COMPOSE": shlex.join(COMPOSE),
            "E2E_REQUIRED": "1",  # a missing stack is a failure here, not a skip
        }
        return subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "e2e"), "-v", *pytest_args],
                              cwd=ROOT, env=test_env).returncode
    finally:
        if keep:
            print(f"\nStack left running: web http://127.0.0.1:{PORTS['WEB_PORT']}  api http://127.0.0.1:{PORTS['API_PORT']}")
            print(f"Stop it with: COMPOSE_PROJECT_NAME={PROJECT} docker compose -f docker-compose.yml -f e2e/docker-compose.e2e.yml down -v")
        else:
            compose("down", "-v", "--remove-orphans", check=False)


if __name__ == "__main__":
    sys.exit(main())

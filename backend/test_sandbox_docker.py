"""The sandbox against real Docker and a real (tiny) model. Skipped unless you opt in:

    docker pull ghcr.io/ggml-org/llama.cpp:server        # or whatever LLAMA_IMAGE you use
    # any small GGUF, e.g. the 1 MB test model from llama.cpp:
    # https://huggingface.co/ggml-org/models/resolve/main/tinyllamas/stories260K.gguf
    TINY_GGUF=/path/to/stories260K.gguf LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:server pytest test_sandbox_docker.py

These tests check what the unit tests can only assume: that Docker really applies the flags,
that the model really loads and answers inside them, and that the container really cannot
reach the internet or write to its filesystem.
"""
import json
import os
import shutil
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

import config
import sandbox
import serving
from main import app

TINY = os.environ.get("TINY_GGUF")

pytestmark = pytest.mark.skipif(
    not (TINY and os.path.exists(TINY or "") and shutil.which("docker")),
    reason="set TINY_GGUF to a small .gguf file and have Docker running to run these",
)

LIMITS = sandbox.Limits(memory="1g", cpus=1.0, pids=256, context=512)


@pytest.fixture(autouse=True)
def real_docker(monkeypatch):
    monkeypatch.setenv("DOCKER_BIN", "docker")  # undo the test-suite default that disables Docker
    try:
        sandbox.docker(["version", "--format", "{{.Server.Version}}"], timeout=15)
    except sandbox.SandboxError as e:
        pytest.skip(str(e))
    image = config.serve_image()
    if sandbox.docker(["image", "inspect", image], timeout=15).returncode != 0:
        pytest.skip(f"image {image} is not pulled")


@pytest.fixture
def running():
    name = f"modelhub-test-{uuid.uuid4().hex[:8]}"
    sandbox.start(name, os.path.abspath(TINY), LIMITS, "chatml", {"modelhub.test": "1"})
    yield name
    sandbox.stop(name)


def sh(name, script):
    """Run a shell snippet inside the container; returns (exit code, combined output)."""
    done = sandbox.docker(["exec", name, "sh", "-c", script], timeout=30)
    return done.returncode, (done.stdout + done.stderr).decode()


def test_the_model_loads_and_answers_inside_the_sandbox(running):
    text = sandbox.chat_once(running, {"messages": [{"role": "user", "content": "Once upon a time"}], "max_tokens": 16})
    assert isinstance(text, str) and text.strip()


def test_streaming_works_through_docker_exec(running):
    payload = {"messages": [{"role": "user", "content": "Once upon a time"}], "max_tokens": 12, "stream": True}
    lines = list(sandbox.chat_stream(running, payload))
    deltas = [json.loads(l[5:])["choices"][0]["delta"].get("content") for l in lines if l.startswith("data:") and "[DONE]" not in l]
    assert any(deltas) and lines[-1].startswith("data: [DONE]") or any(l.startswith("data: [DONE]") for l in lines)


def test_docker_really_applied_the_flags(running):
    out = sandbox.docker(["inspect", running, "--format", "{{json .HostConfig}}"], timeout=15).stdout
    host = json.loads(out)
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True and host["Privileged"] is False
    assert host["CapDrop"] == ["ALL"] and "no-new-privileges" in host["SecurityOpt"]
    assert host["Memory"] == 1024**3 and host["MemorySwap"] == 1024**3
    assert host["PidsLimit"] == 256 and host["NanoCpus"] == 10**9
    assert host["PortBindings"] in (None, {}) and not host.get("Devices")
    mounts = json.loads(sandbox.docker(["inspect", running, "--format", "{{json .Mounts}}"], timeout=15).stdout)
    assert [(m["Destination"], m["RW"]) for m in mounts] == [("/models/model.gguf", False)]


def test_the_container_cannot_reach_the_internet_or_anything_else(running):
    code, _ = sh(running, "curl -s -m 4 -o /dev/null https://example.com")
    assert code != 0
    code, out = sh(running, "cat /proc/net/dev | tail -n +3 | awk '{print $1}' | tr -d ':' | sort | tr '\\n' ' '")
    assert out.strip() == "lo"  # the loopback interface is the only network device that exists


def test_the_container_is_unprivileged_and_cannot_write_to_its_filesystem(running):
    code, out = sh(running, "id -u; touch /pwned 2>&1; touch /models/x 2>&1; touch /tmp/ok && echo tmp-ok")
    assert out.splitlines()[0] == "65534"  # not root
    assert out.count("Read-only file system") == 2 and "tmp-ok" in out
    code, out = sh(running, "grep -E 'CapEff|NoNewPrivs' /proc/self/status")
    assert "CapEff:\t0000000000000000" in out and "NoNewPrivs:\t1" in out
    code, _ = sh(running, "cp /bin/sh /tmp/sh && /tmp/sh -c true")  # noexec tmpfs: cannot run dropped binaries
    assert code != 0


def test_stopping_removes_the_container_and_leftovers_can_be_swept():
    name = f"modelhub-test-{uuid.uuid4().hex[:8]}"
    sandbox.start(name, os.path.abspath(TINY), LIMITS, "chatml", {})
    assert sandbox.is_running(name)
    sandbox.stop(name)
    assert not sandbox.is_running(name)
    assert sandbox.docker(["ps", "-a", "-q", "--filter", f"name={name}"], timeout=15).stdout.strip() == b""

    sandbox.start(name, os.path.abspath(TINY), LIMITS, "chatml", {})
    assert sandbox.stop_all_managed() >= 1  # the label marks it as ours
    assert not sandbox.is_running(name)


def test_a_file_that_is_not_a_model_fails_to_load_cleanly(tmp_path):
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"GGUF" + b"\x03\x00\x00\x00" + b"\x00" * 64)
    name = f"modelhub-test-{uuid.uuid4().hex[:8]}"
    with pytest.raises(sandbox.SandboxError, match="failed to load"):
        sandbox.start(name, str(bad), LIMITS, "chatml", {})
    assert not sandbox.is_running(name)  # and nothing is left behind


def test_full_stack_upload_scan_serve_chat_stop():
    """A real GGUF through the real scanner, the API, and a real container."""
    client = TestClient(app)
    token = client.post("/auth/register", json={"email": "real@example.com", "password": "a long enough pass"}).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    with open(TINY, "rb") as f:
        data = f.read()
    up = client.post("/weights", params={"name": "tiny", "filename": "tiny.gguf"}, content=data,
                     headers={**headers, "Content-Type": "application/octet-stream"})
    assert up.status_code == 201, up.text  # the scanner accepts a genuine llama.cpp model
    wid = up.json()["id"]
    try:
        started = client.post(f"/weights/{wid}/serve", headers=headers)
        assert started.status_code == 200 and started.json()["state"] == "running", started.text
        chat = client.post(f"/weights/{wid}/chat", headers=headers,
                           json={"messages": [{"role": "user", "content": "Once upon a time"}]})
        assert chat.status_code == 200 and chat.json()["reply"].strip()
        streamed = client.post(f"/weights/{wid}/chat", headers=headers,
                               json={"messages": [{"role": "user", "content": "Once"}], "stream": True})
        assert streamed.status_code == 200 and '"delta"' in streamed.text and "[DONE]" in streamed.text
    finally:
        client.delete(f"/weights/{wid}/serve", headers=headers)
        serving.shutdown_all()
    assert client.get(f"/weights/{wid}/serve", headers=headers).json()["state"] == "stopped"
    leftover = subprocess.run(["docker", "ps", "-a", "-q", "--filter", f"name=modelhub-{wid}"], capture_output=True)
    assert leftover.stdout.strip() == b""

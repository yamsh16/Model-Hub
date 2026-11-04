"""The sandbox's container arguments, Docker error handling and chat plumbing (Docker is faked)."""
import json
import subprocess
import sys

import pytest

import config
import sandbox
from sandbox import Limits, SandboxError, SandboxUnavailable

LIMITS = Limits(memory="2g", cpus=2.0, pids=256, context=2048)


def run_args(path="/data/u/m.gguf", template="chatml", labels=None, limits=LIMITS):
    return sandbox.build_run_args("modelhub-x", path, limits, template, labels or {"modelhub.user": "u1"})


def pair(args, flag):
    """The value that follows `flag`."""
    return args[args.index(flag) + 1]


# ---------------------------------------------------------------- what the container is allowed

def test_container_has_no_network_and_a_read_only_unprivileged_filesystem():
    a = run_args()
    assert pair(a, "--network") == "none"
    assert "--read-only" in a
    assert pair(a, "--user") == "65534:65534"  # nobody, not root
    assert pair(a, "--cap-drop") == "ALL"
    assert pair(a, "--security-opt") == "no-new-privileges"
    assert "noexec" in pair(a, "--tmpfs") and "nosuid" in pair(a, "--tmpfs")
    assert "--init" in a


def test_container_resources_are_capped_and_swap_is_off():
    a = run_args()
    assert pair(a, "--memory") == "2g" and pair(a, "--memory-swap") == "2g"  # equal = no swap
    assert pair(a, "--cpus") == "2.0" and pair(a, "--pids-limit") == "256"
    assert pair(a, "--log-opt") == "max-size=1m"


FORBIDDEN = [
    "--privileged", "--cap-add", "--device", "--publish", "-p", "--publish-all", "-P", "--pid", "--ipc",
    "--uts", "--userns", "--volumes-from", "--volume", "-v", "--add-host", "--dns", "--security-opt=seccomp=unconfined",
]


@pytest.mark.parametrize("flag", FORBIDDEN)
def test_nothing_that_weakens_the_sandbox_is_ever_passed(flag):
    assert flag not in run_args()


def test_no_part_of_the_command_exposes_the_docker_socket_or_host():
    joined = " ".join(run_args())
    for bad in ("docker.sock", "/var/run", "--network host", "host", "unconfined"):
        assert bad not in joined.replace("--host", "")  # llama-server's own --host 127.0.0.1 is fine


def test_the_only_mount_is_the_weights_file_read_only():
    a = run_args("/data/u1/m1.gguf")
    mounts = [a[i + 1] for i, x in enumerate(a) if x == "--mount"]
    assert mounts == ["type=bind,source=/data/u1/m1.gguf,target=/models/model.gguf,readonly"]


def test_images_are_never_pulled_implicitly_and_the_default_is_pinned_by_digest():
    assert pair(run_args(), "--pull") == "never"
    assert "@sha256:" in config.DEFAULT_LLAMA_IMAGE


def test_llama_server_runs_without_jinja_webui_or_embedded_template():
    a = run_args(template="llama3")
    tail = a[a.index(config.serve_image()) + 1:]
    assert "--no-jinja" in tail and "--no-webui" in tail
    assert pair(tail, "--chat-template") == "llama3"
    assert pair(tail, "--host") == "127.0.0.1"
    assert pair(tail, "-m") == "/models/model.gguf"


def test_every_container_is_labelled_so_leftovers_can_be_found():
    a = run_args(labels={"modelhub.user": "u1", "modelhub.model": "m1"})
    labels = [a[i + 1] for i, x in enumerate(a) if x == "--label"]
    assert "modelhub.managed=1" in labels and "modelhub.user=u1" in labels and "modelhub.model=m1" in labels


@pytest.mark.parametrize("template", ["", "chatml; rm -rf /", "../../x", "{{ 7*7 }}", "CHATML", "jinja", "custom"])
def test_only_allow_listed_templates_are_accepted(template):
    with pytest.raises(SandboxError, match="Unknown chat template"):
        run_args(template=template)


def test_every_allowed_template_is_accepted():
    for t in sandbox.CHAT_TEMPLATES:
        assert pair(run_args(template=t)[-2:], "--chat-template") == t


def test_a_comma_in_the_path_cannot_smuggle_in_extra_mount_options():
    with pytest.raises(SandboxError, match="comma"):
        run_args(path="/data/x,bind-propagation=shared/m.gguf")


def test_prompts_are_never_part_of_any_command_line(monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b'{"choices":[{"message":{"content":"ok"}}]}', stderr=b"")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    secret = "TOP-SECRET-PROMPT $(reboot) `id` ; rm -rf /"
    sandbox.chat_once("modelhub-x", {"messages": [{"role": "user", "content": secret}]})
    assert seen and not any(secret in part for part in seen[0])


def test_limits_come_from_config_and_validate(monkeypatch):
    monkeypatch.setenv("SERVE_MEMORY", "512m")
    monkeypatch.setenv("SERVE_CPUS", "1")
    assert sandbox.limits_from_config() == Limits(memory="512m", cpus=1.0, pids=256, context=2048)
    assert sandbox.memory_bytes("512m") == 512 * 1024**2 and sandbox.memory_bytes("2G") == 2 * 1024**3
    for bad in ("", "lots", "-1g", "1.5g", "2gb"):
        with pytest.raises(ValueError):
            sandbox.memory_bytes(bad)
    with pytest.raises(ValueError):
        run_args(limits=Limits(memory="two gigs", cpus=1, pids=1, context=1))


# ------------------------------------------------------------------------------ Docker access

def test_missing_docker_binary_is_reported_as_unavailable():
    with pytest.raises(SandboxUnavailable, match="not installed"):
        sandbox.docker(["ps"])  # conftest points DOCKER_BIN at a binary that does not exist


@pytest.mark.parametrize(
    "stderr",
    [
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        "error during connect: Get http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine: open //./pipe/x",
        "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine",
    ],
)
def test_a_stopped_docker_daemon_is_reported_as_unavailable(monkeypatch, stderr):
    monkeypatch.setattr(
        sandbox.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=stderr.encode())
    )
    with pytest.raises(SandboxUnavailable, match="not running"):
        sandbox.docker(["ps"])


def test_a_hung_docker_command_times_out_cleanly(monkeypatch):
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(sandbox.subprocess, "run", hang)
    with pytest.raises(SandboxError, match="did not answer"):
        sandbox.docker(["ps"])


class FakeDocker:
    """Scripted `docker` replies, keyed by the subcommand."""

    def __init__(self, **replies):
        self.replies, self.calls = replies, []

    def __call__(self, args, **kw):
        self.calls.append(args)
        sub = args[0] if args[0] != "exec" else ("health" if "/health" in " ".join(args) else "exec")
        reply = self.replies.get(sub, (0, b"", b""))
        if callable(reply):
            reply = reply(args)
        code, out, err = reply
        return subprocess.CompletedProcess(args, code, stdout=out, stderr=err)

    def ran(self, sub):
        return [c for c in self.calls if c[0] == sub]


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)


def test_start_waits_for_the_model_to_load(monkeypatch):
    healths = iter([(0, b"503", b""), (0, b"503", b""), (0, b"200", b"")])
    fake = FakeDocker(inspect=(0, b"true", b""), health=lambda a: next(healths))
    monkeypatch.setattr(sandbox, "docker", fake)
    sandbox.start("modelhub-x", "/p/m.gguf", LIMITS, "chatml", {})
    assert len(fake.ran("run")) == 1 and len(fake.ran("rm")) == 0  # kept running


def test_start_reports_a_missing_runtime_image_helpfully(monkeypatch):
    monkeypatch.setattr(sandbox, "docker", FakeDocker(run=(125, b"", b"Unable to find image 'x' locally")))
    with pytest.raises(SandboxError, match="docker pull"):
        sandbox.start("modelhub-x", "/p/m.gguf", LIMITS, "chatml", {})


def test_start_reports_why_a_model_failed_to_load_and_cleans_up(monkeypatch):
    fake = FakeDocker(
        inspect=(0, b"false", b""),
        logs=(0, b"E llama_model_load: failed to load model\nE exiting", b""),
    )
    monkeypatch.setattr(sandbox, "docker", fake)
    with pytest.raises(SandboxError, match="failed to load model"):
        sandbox.start("modelhub-x", "/p/m.gguf", LIMITS, "chatml", {})
    assert fake.ran("rm") == [["rm", "-f", "modelhub-x"]]  # nothing left behind


def test_start_gives_up_after_the_timeout_and_cleans_up(monkeypatch):
    monkeypatch.setenv("SERVE_START_TIMEOUT", "0.05")
    clock = iter(x * 0.04 for x in range(1000))
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: next(clock))
    fake = FakeDocker(inspect=(0, b"true", b""), health=(0, b"503", b""))
    monkeypatch.setattr(sandbox, "docker", fake)
    with pytest.raises(SandboxError, match="too long"):
        sandbox.start("modelhub-x", "/p/m.gguf", LIMITS, "chatml", {})
    assert fake.ran("rm")


def test_stop_all_managed_removes_only_labelled_containers(monkeypatch):
    fake = FakeDocker(ps=(0, b"abc123\ndef456\n", b""))
    monkeypatch.setattr(sandbox, "docker", fake)
    assert sandbox.stop_all_managed() == 2
    assert fake.ran("ps")[0][-2:] == ["--filter", "label=modelhub.managed=1"]
    assert fake.ran("rm") == [["rm", "-f", "abc123", "def456"]]
    monkeypatch.setattr(sandbox, "docker", FakeDocker(ps=(0, b"", b"")))
    assert sandbox.stop_all_managed() == 0


# ------------------------------------------------------------------------------------ chatting

def reply(content="hi"):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()


def test_chat_once_returns_the_reply_and_sends_the_prompt_on_stdin(monkeypatch):
    captured = {}

    def fake(args, *, input=None, timeout=0):
        captured.update(args=args, input=input)
        return subprocess.CompletedProcess(args, 0, stdout=reply("hello"), stderr=b"")

    monkeypatch.setattr(sandbox, "docker", fake)
    assert sandbox.chat_once("modelhub-x", {"messages": [{"role": "user", "content": "yo"}]}) == "hello"
    assert json.loads(captured["input"])["messages"][0]["content"] == "yo"
    assert captured["args"][:4] == ["exec", "-i", "modelhub-x", "curl"]
    assert "http://127.0.0.1:8080/v1/chat/completions" in captured["args"]  # inside the container only


@pytest.mark.parametrize(
    "stdout,code,stderr,match",
    [
        (b'{"error":{"message":"context too long"}}', 0, b"", "context too long"),
        (b"not json", 0, b"", "unreadable"),
        (b'{"nope":1}', 0, b"", "unexpected"),
        (b"", 1, b"Error response from daemon: container x is not running", "stopped"),
        (b"", 28, b"curl: (28) Operation timed out", "too long"),
        (b"", 7, b"curl: (7) Failed to connect", "did not answer"),
    ],
)
def test_chat_once_failures_become_readable_errors(monkeypatch, stdout, code, stderr, match):
    monkeypatch.setattr(
        sandbox, "docker", lambda args, **kw: subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)
    )
    with pytest.raises(SandboxError, match=match):
        sandbox.chat_once("modelhub-x", {"messages": []})


FAKE_CLIENT = r"""
import sys, time, json
payload = json.loads(sys.stdin.read())
mode = payload["messages"][0]["content"]
if mode == "stream":
    for word in ("Hel", "lo"):
        print("data: " + json.dumps({"choices": [{"delta": {"content": word}}]}), flush=True)
        print(flush=True)
    print("data: [DONE]", flush=True)
elif mode == "hang":
    print("data: " + json.dumps({"choices": [{"delta": {"content": "first"}}]}), flush=True)
    time.sleep(60)
"""


def use_fake_client(monkeypatch):
    monkeypatch.setattr(sandbox, "_docker_argv", lambda args: [sys.executable, "-c", FAKE_CLIENT])


def test_chat_stream_yields_server_sent_event_lines(monkeypatch):
    use_fake_client(monkeypatch)
    lines = list(sandbox.chat_stream("modelhub-x", {"messages": [{"role": "user", "content": "stream"}]}))
    data = [l for l in lines if l.startswith("data:")]
    assert [json.loads(l[5:])["choices"][0]["delta"]["content"] for l in data[:2]] == ["Hel", "lo"]
    assert data[-1] == "data: [DONE]"


def test_chat_stream_kills_the_process_when_the_client_goes_away(monkeypatch):
    use_fake_client(monkeypatch)
    procs = []
    real_popen = subprocess.Popen

    def spy(*a, **kw):
        p = real_popen(*a, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(sandbox.subprocess, "Popen", spy)
    gen = sandbox.chat_stream("modelhub-x", {"messages": [{"role": "user", "content": "hang"}]})
    assert next(gen).startswith("data:")  # got the first token; the "model" now stalls
    gen.close()  # the browser disconnected
    assert procs[0].poll() is not None  # the exec process was killed, not left running


def test_chat_stream_watchdog_kills_a_stalled_model(monkeypatch):
    use_fake_client(monkeypatch)
    monkeypatch.setenv("SERVE_REQUEST_TIMEOUT", "-4.5")  # watchdog fires at timeout + 5 = 0.5 s
    start = __import__("time").monotonic()
    lines = list(sandbox.chat_stream("modelhub-x", {"messages": [{"role": "user", "content": "hang"}]}))
    assert __import__("time").monotonic() - start < 10  # did not wait for the 60 s sleep
    assert any(l.startswith("data:") for l in lines)

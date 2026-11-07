"""End-to-end tests of the whole Docker stack: web, API, PostgreSQL and a stub model, over real HTTP.

What these add to the unit suite: the images build and start, migrations create the schema on a
fresh PostgreSQL, the web bundle points at the API, secrets and uploads survive a restart, and the
containers are locked down as configured. The user journeys are the same ones the app is for.
"""
import io
import json
import pickle
import re
import zipfile
from urllib.parse import urlparse

import httpx
import pytest

from conftest import API, STUB_URL, WEB, User, compose, sign_up, wait_until_healthy
from weights_fixtures import gguf_bytes, safetensors_bytes

ENDPOINT = {"name": "stub", "url": STUB_URL, "model": "stub-1", "api_key": "sk-e2e-key"}
# 20 rows. The stub gets the two "tricky" ones wrong, so accuracy is exactly 18/20.
ROWS = (
    [(f"great film {i}", "pos") for i in range(9)] + [("great tricky film", "pos")]
    + [(f"awful film {i}", "neg") for i in range(9)] + [("awful tricky film", "neg")]
)
CSV = "text,label\n" + "".join(f"{t},{label}\n" for t, label in ROWS)
LOG = "epoch,loss,val_loss\n" + "".join(f"{i},{1 / (i + 1):.4f},{1.2 / (i + 1):.4f}\n" for i in range(15))


def upload_weights(user: User, name: str, filename: str, data: bytes) -> httpx.Response:
    return user.post("/weights", params={"name": name, "filename": filename}, content=data,
                     headers={"Content-Type": "application/octet-stream"})


# ---------- the stack itself ----------

def test_every_page_is_served_and_the_bundle_points_at_the_api(http):
    for path in ("/", "/connect", "/report", "/chat"):
        assert http.get(f"{WEB}{path}").status_code == 200, path
    html = http.get(f"{WEB}/connect").text
    scripts = sorted(set(re.findall(r'/_next/static/[^"\'\s]+\.js', html)))
    assert scripts, "no script bundles found in the page"
    port = urlparse(API).port
    bundle = "".join(http.get(f"{WEB}{s}").text for s in scripts)
    assert re.search(rf"://(localhost|127\.0\.0\.1):{port}\b", bundle), "the bundle was not built with this API address"


def test_api_health_and_protective_headers(http):
    r = http.get(f"{API}/health")
    assert r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_cors_allows_the_web_app_and_nobody_else(http):
    port = urlparse(WEB).port
    for origin in (f"http://localhost:{port}", f"http://127.0.0.1:{port}"):
        r = http.options(f"{API}/endpoints", headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                                      "Access-Control-Request-Headers": "authorization"})
        assert r.headers.get("access-control-allow-origin") == origin
    r = http.options(f"{API}/endpoints", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_nothing_works_without_signing_in(http):
    for path in ("/endpoints", "/weights", "/auth/me"):
        assert http.get(f"{API}{path}").status_code == 401, path
    assert http.get(f"{API}/endpoints", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_sign_in_with_the_registered_password(http, alice):
    r = http.post(f"{API}/auth/login", json={"email": alice.email, "password": "an e2e test password"})
    assert r.status_code == 200
    assert http.post(f"{API}/auth/login", json={"email": alice.email, "password": "wrong password!"}).status_code == 401
    assert alice.get("/auth/me").json()["email"] == alice.email


# ---------- the product journey ----------

def test_the_whole_journey(alice):
    # attach a model; the key goes in, is stored encrypted and never comes back out
    r = alice.post("/endpoints", json=ENDPOINT)
    assert r.status_code == 201, r.text
    endpoint = r.json()
    assert "api_key" not in endpoint and "sk-e2e-key" not in r.text

    # test set + target + a scored evaluation with a known answer
    testset = alice.post("/testsets", files={"file": ("reviews.csv", CSV)}).json()
    assert testset["rows"] == 20
    assert alice.put(f"/testsets/{testset['id']}/target", json={"column": "label"}).status_code == 200
    r = alice.post(f"/testsets/{testset['id']}/evaluate", json={"endpoint_id": endpoint["id"]})
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["n"] == 20 and ev["unparsed"] == 0
    assert ev["accuracy"] == pytest.approx(0.9)
    assert sorted(ev["classes"]) == ["neg", "pos"]
    assert sum(map(sum, ev["confusion_matrix"])) == 20

    # train/test leakage: the first five test rows reused as training data
    train = "text,label\n" + "".join(f"{t},{label}\n" for t, label in ROWS[:5])
    leak = alice.post(f"/testsets/{testset['id']}/leakage", files={"train_file": ("train.csv", train)}).json()
    assert leak["overlap_rows"] == 5 and leak["warning"]

    # training log -> estimate -> readiness built from all of it
    log = alice.post("/logs", files={"file": ("run.csv", LOG)}).json()
    estimate = alice.get(f"/logs/{log['id']}/estimate")
    assert estimate.status_code == 200 and estimate.json()["n_points"] > 5
    ready = alice.get("/readiness", params={"testset_id": testset["id"], "log_id": log["id"]}).json()
    assert 0 <= ready["score"] <= 100 and ready["components"] and ready["advice"]

    # chat, plain and streamed
    chat = alice.post(f"/endpoints/{endpoint['id']}/chat", json={"messages": [{"role": "user", "content": "hello there"}]})
    assert chat.json()["reply"] == "echo: hello there"
    with alice.http.stream("POST", f"{API}/endpoints/{endpoint['id']}/chat", headers=alice.headers,
                           json={"messages": [{"role": "user", "content": "hello there"}], "stream": True}) as s:
        assert s.headers["content-type"].startswith("text/event-stream")
        events = [line[6:] for line in s.iter_lines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    assert "".join(json.loads(e)["delta"] for e in events[:-1]).strip() == "echo: hello there"

    # listed, then deleted
    assert [e["id"] for e in alice.get("/endpoints").json()] == [endpoint["id"]]
    assert alice.delete(f"/endpoints/{endpoint['id']}").status_code == 204
    assert alice.get(f"/endpoints/{endpoint['id']}").status_code == 404


def test_a_bad_key_is_reported_not_stored(alice):
    r = alice.post("/endpoints", json={**ENDPOINT, "api_key": "wrong"})
    assert r.status_code == 400 and "API key" in r.json()["detail"]
    assert alice.get("/endpoints").json() == []


def test_users_cannot_see_or_use_each_others_data(alice, bob):
    endpoint = alice.post("/endpoints", json=ENDPOINT).json()
    testset = alice.post("/testsets", files={"file": ("t.csv", CSV)}).json()
    log = alice.post("/logs", files={"file": ("l.csv", LOG)}).json()
    weights = upload_weights(alice, "mine", "w.safetensors", safetensors_bytes()).json()

    for path in (f"/endpoints/{endpoint['id']}", f"/testsets/{testset['id']}", f"/logs/{log['id']}/estimate",
                 f"/weights/{weights['id']}"):
        assert bob.get(path).status_code == 404, path
    assert bob.delete(f"/endpoints/{endpoint['id']}").status_code == 404
    assert bob.delete(f"/weights/{weights['id']}").status_code == 404
    assert bob.post(f"/endpoints/{endpoint['id']}/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 404
    mine = bob.post("/endpoints", json=ENDPOINT).json()
    assert bob.put(f"/testsets/{testset['id']}/target", json={"column": "label"}).status_code == 404
    assert bob.post(f"/testsets/{testset['id']}/evaluate", json={"endpoint_id": mine["id"]}).status_code == 404
    assert bob.get("/weights").json() == []
    assert [e["id"] for e in bob.get("/endpoints").json()] == [mine["id"]]
    # and Alice still has everything
    assert alice.get(f"/weights/{weights['id']}").status_code == 200


# ---------- uploads ----------

def test_a_genuine_safetensors_file_is_accepted_listed_and_deleted(alice):
    r = upload_weights(alice, "tiny", "tiny.safetensors", safetensors_bytes())
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["format"] == "safetensors" and w["tensor_count"] == 1 and w["serve_state"] == "unsupported"
    assert [x["id"] for x in alice.get("/weights").json()] == [w["id"]]
    assert alice.delete(f"/weights/{w['id']}").status_code == 204
    assert alice.get("/weights").json() == []


class Payload:
    def __reduce__(self):  # would run if anything unpickled it; here it is only ever sent, never loaded
        return (exec, ("open('/tmp/pwned', 'w').write('x')",))


@pytest.mark.parametrize("filename", ["model.pkl", "model.pt", "MODEL.BIN", "model.pt.", "model.safetensors", "model.gguf"])
def test_hostile_uploads_are_refused_and_nothing_is_stored(alice, filename):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("archive/data.pkl", pickle.dumps(Payload(), protocol=2))
    for body in (pickle.dumps(Payload(), protocol=2), archive.getvalue()):
        r = upload_weights(alice, "bad", filename, body)
        assert r.status_code == 422, (filename, r.status_code, r.text)
        assert "Rejected" in r.json()["detail"] or "Only" in r.json()["detail"]
    assert alice.get("/weights").json() == []


def test_serving_without_a_docker_socket_fails_cleanly(alice, http):
    w = upload_weights(alice, "chatty", "chatty.gguf", gguf_bytes()).json()
    assert w["format"] == "gguf" and w["serve_state"] == "stopped"
    r = alice.post(f"/weights/{w['id']}/serve")
    assert r.status_code == 503, r.text  # unavailable, not a crash
    assert "Docker" in r.json()["detail"]
    assert alice.get(f"/weights/{w['id']}/serve").json()["state"] == "stopped"
    assert http.get(f"{API}/health").status_code == 200  # and the API is unharmed


# ---------- operating the stack ----------

def test_sign_ins_keys_and_uploads_survive_replacing_the_api_container(alice, http):
    endpoint = alice.post("/endpoints", json=ENDPOINT).json()
    weights = upload_weights(alice, "kept", "kept.safetensors", safetensors_bytes()).json()

    # a brand-new container: only what is in the volumes and the database can survive this
    done = compose("up", "-d", "--force-recreate", "--no-deps", "--wait", "api")
    assert done.returncode == 0, done.stderr
    wait_until_healthy(http)

    assert alice.get("/auth/me").json()["email"] == alice.email  # the signing secret was kept
    assert [e["id"] for e in alice.get("/endpoints").json()] == [endpoint["id"]]  # rows are in the database
    reply = alice.post(f"/endpoints/{endpoint['id']}/chat", json={"messages": [{"role": "user", "content": "still there?"}]})
    assert reply.json()["reply"] == "echo: still there?"  # the encryption key was kept: the stored API key still decrypts
    assert alice.get(f"/weights/{weights['id']}").json()["sha256"] == weights["sha256"]
    assert sign_up(http).token  # and new accounts still work


def test_containers_are_locked_down():
    probe = (
        "import os\n"
        "print('uid', os.getuid())\n"
        "print('socket', os.path.exists('/var/run/docker.sock'))\n"
        "for path in ('/app/x', '/etc/x', '/data/x'):  # /data is owned by the app user: only read_only protects it\n"
        "    try:\n"
        "        open(path, 'w'); print('writable', path)\n"
        "    except OSError:\n"
        "        print('readonly', path)\n"
        "open('/data/uploads/.probe', 'w').close(); print('uploads writable')\n"
    )
    out = compose("exec", "-T", "api", "python", "-c", probe)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.split("\n")
    assert "uid 10001" in lines  # not root
    assert "socket False" in lines  # no Docker control
    assert {"readonly /app/x", "readonly /etc/x", "readonly /data/x"} <= set(lines)
    assert "uploads writable" in lines  # the one place it may write
    web = compose("exec", "-T", "web", "sh", "-c", "id -u; touch /app/x 2>&1 | head -1")
    assert web.stdout.split("\n")[0] != "0", "the web container runs as root"
    assert "ead-only" in web.stdout

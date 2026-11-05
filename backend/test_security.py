"""Security tests: hostile uploads, filename tricks, scanner cost limits, unsafe-code checks,
response headers. The focus is the decision made on Day 27: pickle-based and other code-bearing
formats are REFUSED, never loaded, and nothing in the codebase can be made to unpickle.

The key trick is a real payload: a pickle that writes a marker file if (and only if) something
unpickles it. A control test proves the payload really works; every other test then proves it
was never run.
"""
import ast
import gzip
import io
import json
import pickle
import re
import struct
import subprocess
import sys
import tarfile
import time
import unicodedata
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import scan
from main import app
from weights_fixtures import gguf_bytes, safetensors_bytes

client = TestClient(app)
BACKEND = Path(__file__).parent


# --------------------------------------------------------------------------- a real payload

class Payload:
    """Unpickling this runs `exec(...)`, which creates the marker file."""

    def __init__(self, marker):
        self.marker = str(marker)

    def __reduce__(self):
        return (exec, (f"open({self.marker!r}, 'w').write('pwned')",))


@pytest.fixture
def marker(tmp_path):
    return tmp_path / "PWNED"


def pickles(marker):
    """The same payload, in every pickle protocol."""
    return {f"pickle-protocol-{p}": pickle.dumps(Payload(marker), protocol=p) for p in range(0, 6)}


def torch_style_zip(marker, name="archive/data.pkl"):
    """A PyTorch .pt/.pth checkpoint is a zip whose data.pkl is a pickle."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, pickle.dumps(Payload(marker), protocol=2))
        z.writestr("archive/version", "3\n")
    return buf.getvalue()


def artifacts(marker):
    import numpy as np

    out = dict(pickles(marker))
    out["torch-zip"] = torch_style_zip(marker)
    out["torch-zip-nested"] = torch_style_zip(marker, "model/sub/dir/data.pkl")
    out["gzip-of-pickle"] = gzip.compress(pickle.dumps(Payload(marker)))
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as tf:
        raw = pickle.dumps(Payload(marker))
        info = tarfile.TarInfo("data.pkl")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    out["tar-of-pickle"] = tar.getvalue()
    npy = io.BytesIO()
    np.save(npy, np.array([Payload(marker)], dtype=object), allow_pickle=True)  # numpy pickles object arrays
    out["numpy-object-array"] = npy.getvalue()
    return out


def test_control_the_payload_really_runs_if_something_unpickles_it(tmp_path):
    """If this fails the other tests would prove nothing. Runs in a throwaway child process."""
    marker = tmp_path / "control-marker"
    blob = tmp_path / "blob.pkl"
    blob.write_bytes(pickle.dumps(Payload(marker)))
    subprocess.run([sys.executable, "-I", "-c", "import pickle,sys; pickle.loads(open(sys.argv[1],'rb').read())", str(blob)],
                   check=True, timeout=30)
    assert marker.read_text() == "pwned"


# -------------------------------------------------------------- hostile files, every disguise

REFUSED_NAMES = [
    "model.pkl", "model.pt", "model.pth", "model.bin", "model.ckpt", "model.joblib", "model.npy", "model.npz",
    "model.h5", "model.onnx", "model.zip", "model.tar.gz", "model.exe",
    "MODEL.PT", "Model.PkL",  # letter case does not matter
    "model.pt.", "model.pt ", "model.pt . ",  # Windows ignores trailing dots and spaces
    "model.safetensors.pt", "model.gguf.pkl",  # the last extension decides
    "../../model.pkl", "..\\..\\model.pt", "/etc/model.bin",
]
DISGUISED_NAMES = ["model.safetensors", "model.gguf", "MODEL.SAFETENSORS", "model.GGUF", "model.pt.safetensors"]


def upload(data, filename):
    return client.post("/weights", params={"name": "x", "filename": filename}, content=data,
                       headers={"Content-Type": "application/octet-stream"})


def stored():
    root = config.upload_dir()
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_every_hostile_artifact_under_every_refused_name_is_rejected_and_never_run(marker, database):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    import models

    for label, data in artifacts(marker).items():
        for name in REFUSED_NAMES:
            r = upload(data, name)
            assert r.status_code == 422, (label, name, r.status_code)
            assert "not accepted" in r.json()["detail"] or "Only .safetensors" in r.json()["detail"]
    assert not marker.exists(), "an uploaded payload was executed"
    assert stored() == []
    with Session(database) as s:
        assert s.scalar(select(models.Model)) is None


def test_every_hostile_artifact_renamed_to_a_good_extension_is_still_rejected_by_content(marker, database):
    for label, data in artifacts(marker).items():
        for name in DISGUISED_NAMES:
            r = upload(data, name)
            assert r.status_code == 422, (label, name, r.status_code, r.text[:120])
            assert "Rejected" in r.json()["detail"] or "not GGUF" in r.json()["detail"] or "safetensors" in r.json()["detail"]
    assert not marker.exists(), "an uploaded payload was executed"
    assert stored() == []


def test_the_scanner_itself_never_executes_what_it_reads(marker, tmp_path):
    for label, data in artifacts(marker).items():
        path = tmp_path / f"{label}.bin"
        path.write_bytes(data)
        for fmt in ("safetensors", "gguf"):
            with pytest.raises(scan.ScanError):
                scan.scan_file(str(path), fmt)
        assert scan.sniff_unsafe(data[:65536]) is not None, f"{label}: not even recognised"
    assert not marker.exists()


def test_rejections_name_the_danger_and_say_how_to_convert(marker):
    msg = upload(pickle.dumps(Payload(marker)), "model.pkl").json()["detail"]
    assert "runs arbitrary code" in msg and "safetensors.torch.save_file" in msg
    zipped = upload(torch_style_zip(marker), "weights.safetensors").json()["detail"]
    assert "ZIP archive" in zipped and "Convert it to safetensors" in zipped
    assert "not supported yet" in upload(b"x", "m.onnx").json()["detail"]
    assert "archives are never unpacked" in upload(b"x", "m.zip").json()["detail"]
    assert "Keras" in upload(b"x", "m.h5").json()["detail"]


def test_a_zip_bomb_is_rejected_without_being_unpacked(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a", b"\0" * 50_000_000)  # 50 MB of zeros, ~50 KB zipped
    assert len(buf.getvalue()) < 100_000
    start = time.monotonic()
    assert upload(buf.getvalue(), "weights.safetensors").status_code == 422
    assert time.monotonic() - start < 5
    assert stored() == []


# --------------------------------------------------------------------- polyglots and padding

def test_pickle_bytes_inside_a_valid_safetensors_data_section_are_just_data(marker):
    """Valid safetensors may hold any bytes as tensor data. That is fine: nothing ever interprets
    tensor data as code, and the test shows the payload still did not run."""
    blob = pickle.dumps(Payload(marker), protocol=2)
    data = safetensors_bytes({"w": ("U8", [len(blob)], blob)})
    r = upload(data, "model.safetensors")
    assert r.status_code == 201
    assert not marker.exists()
    (path,) = stored()
    assert path.read_bytes() == data  # stored verbatim, never loaded


def test_text_in_safetensors_metadata_is_never_interpreted(marker):
    nasty = pickle.dumps(Payload(marker), protocol=0).decode("latin-1")
    data = safetensors_bytes(metadata={"note": nasty, "k": "{{ 7*7 }}", "x": "$(reboot)"})
    r = upload(data, "model.safetensors")
    assert r.status_code == 201 and not marker.exists()
    assert r.json()["metadata"]["k"] == "{{ 7*7 }}"  # shown as plain text, not evaluated


@pytest.mark.parametrize(
    "tail",
    [b"PK\x03\x04" + b"\0" * 64, b"\x80\x02}q\x00.", b"MZ" + b"\0" * 200, b"A" * 4096],
    ids=["zip-header", "tiny-pickle", "exe-header", "4k-of-text"],
)
def test_payloads_appended_to_a_gguf_are_rejected(tmp_path, tail):
    good = gguf_bytes()
    scan_ok = tmp_path / "ok.gguf"
    scan_ok.write_bytes(good)
    assert scan.scan_file(str(scan_ok), "gguf").tensor_count == 1
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(good + tail)
    with pytest.raises(scan.ScanError, match="extra bytes"):
        scan.scan_file(str(bad), "gguf")


def test_real_gguf_files_are_not_hurt_by_the_trailing_bytes_check(tmp_path):
    np = pytest.importorskip("numpy")
    gguf = pytest.importorskip("gguf")
    for dims, dtype in (((16, 32), "float32"), ((8, 32), "float16"), ((3, 5), "float32"), ((1, 1), "float32")):
        path = tmp_path / f"real-{dims[0]}.gguf"
        w = gguf.GGUFWriter(str(path), "llama")
        w.add_tensor("t", np.random.rand(*dims).astype(dtype))
        w.write_header_to_file()
        w.write_kv_data_to_file()
        w.write_tensors_to_file()
        w.close()
        assert scan.scan_file(str(path), "gguf").tensor_count == 1, dims


# ----------------------------------------------- a bug the pickle sniffer once caused (Day 25)

def test_valid_safetensors_whose_first_bytes_look_like_a_pickle_header_are_accepted(tmp_path):
    """A safetensors header of 640 bytes starts with bytes 80 02: the same as pickle protocol 2.
    The scanner once refused such files as pickles. Recognising a pickle must never be what
    decides, only what explains."""
    for header_len in (640, 896, 1152, 1408):  # first two bytes 0x80, 0x02..0x05
        header = {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
        raw = json.dumps(header).encode().ljust(header_len, b" ")
        data = struct.pack("<Q", len(raw)) + raw + b"\0" * 4
        assert data[0] == 0x80 and 2 <= data[1] <= 5
        path = tmp_path / f"h{header_len}.safetensors"
        path.write_bytes(data)
        assert scan.scan_file(str(path), "safetensors").tensor_count == 1, header_len


def test_no_legitimate_safetensors_header_length_is_ever_mistaken_for_something_else(tmp_path):
    path = tmp_path / "x.safetensors"
    for header_len in range(64, 8192 + 1, 8):
        header = {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
        raw = json.dumps(header).encode().ljust(header_len, b" ")
        path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * 4)
        assert scan.scan_file(str(path), "safetensors").tensor_count == 1, header_len


def test_ordinary_text_is_not_called_a_pickle():
    for text in (b"hello world, just text\n", b"I think this is a README file.\n", b"The quick brown fox",
                 b'{"a": 1}', b"c = 3\n", b"(a, b)\n", b"S'x'"):
        assert scan.sniff_unsafe(text) is None, text


# ------------------------------------------------------------------------------- filenames

@pytest.mark.parametrize(
    "filename",
    [
        "m.safetensors", "M.SAFETENSORS", "m.safetensors.", "m.safetensors ", "a b.safetensors", "ünïcode-模型.safetensors",
        "m.pt.safetensors", "x.pkl\x00.safetensors", "evil‮txt.safetensors", "zero​width.safetensors",
        "../../up/m.safetensors", "..\\..\\up\\m.safetensors", "/etc/passwd.safetensors", "a/b/c.safetensors",
        "m.safetensors?x=1&name=2#frag", "a%2e%2e%2fm.safetensors", "CON.safetensors", "a" * 200 + ".safetensors",
    ],
)
def test_accepted_filenames_never_choose_where_the_file_goes_or_what_it_is_called(tmp_path, filename):
    r = upload(safetensors_bytes(), filename)
    if r.status_code == 422:  # e.g. an extension that is not ours after normalising
        assert stored() == []
        return
    assert r.status_code == 201, (filename, r.text)
    (path,) = stored()
    root = config.upload_dir().resolve()
    assert root in path.resolve().parents
    assert re.fullmatch(r"[0-9a-f-]{36}\.safetensors", path.name)  # an id plus a fixed extension
    shown = r.json()["filename"]
    assert not any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in shown)
    assert "/" not in shown and "\\" not in shown
    assert not list(tmp_path.glob("*.safetensors"))  # nothing escaped the upload folder


@pytest.mark.parametrize("filename", ["m.safetensors", "m.pt", "m.pkl.safetensors", "m"])
def test_the_stored_extension_always_comes_from_the_allow_list(filename):
    r = upload(safetensors_bytes() if filename.endswith("safetensors") else b"x", filename)
    for path in stored():
        assert path.suffix in (".safetensors", ".gguf")
    assert r.status_code in (201, 422)


# ---------------------------------------------------------------------------- cost limits

def test_a_header_with_a_huge_number_of_tensors_is_scanned_in_bounded_time(tmp_path):
    n = 100_000
    tensors = {f"t{i}": ("U8", [1], b"\0") for i in range(n)}
    path = tmp_path / "many.safetensors"
    path.write_bytes(safetensors_bytes(tensors))
    start = time.monotonic()
    assert scan.scan_file(str(path), "safetensors").tensor_count == n
    assert time.monotonic() - start < 15


def test_a_gguf_with_many_metadata_entries_and_tensors_is_scanned_in_bounded_time(tmp_path):
    from weights_fixtures import gguf_u32

    kvs = [(f"key.{i}", 4, gguf_u32(i)) for i in range(50_000)]
    tensors = [(f"t{i}", [32], 0, i * 128) for i in range(5_000)]
    path = tmp_path / "many.gguf"
    path.write_bytes(gguf_bytes(kvs=kvs, tensors=tensors, data_len=5_000 * 128))
    start = time.monotonic()
    assert scan.scan_file(str(path), "gguf").tensor_count == 5_000
    assert time.monotonic() - start < 15


def test_claims_far_bigger_than_the_file_cost_nothing(tmp_path):
    start = time.monotonic()
    for data, fmt in (
        (struct.pack("<Q", 99_000_000) + b"{", "safetensors"),
        (gguf_bytes(tensor_count=900_000, kv_count=90_000), "gguf"),
        (b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", 10**12), "gguf"),
    ):
        path = tmp_path / "claim"
        path.write_bytes(data)
        with pytest.raises(scan.ScanError):
            scan.scan_file(str(path), fmt)
    assert time.monotonic() - start < 5


def test_the_pickle_recogniser_is_fast_on_pathological_input():
    for blob in (b"(" * 65536, b"c" * 65536, b"\x80\x02" + b"(" * 65534, b"S'" + b"a" * 65000, bytes(range(256)) * 256):
        start = time.monotonic()
        scan.sniff_unsafe(blob)
        assert time.monotonic() - start < 2


# -------------------------------------------- nothing in the codebase can unpickle or eval

FORBIDDEN_MODULES = {"pickle", "cPickle", "_pickle", "dill", "cloudpickle", "joblib", "marshal", "shelve", "torch", "keras", "tensorflow"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_ATTRS = {("os", "system"), ("os", "popen"), ("yaml", "load"), ("np", "load"), ("numpy", "load"), ("torch", "load")}
SUBPROCESS_ALLOWED_IN = {"sandbox.py"}  # the only module that starts other programs (Docker)


def unsafe_constructs(source: str, filename: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
            found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in FORBIDDEN_CALLS:
                found.append(f"{f.id}() at line {node.lineno}")
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and (f.value.id, f.attr) in FORBIDDEN_ATTRS:
                found.append(f"{f.value.id}.{f.attr}() at line {node.lineno}")
            if any(k.arg == "shell" and getattr(k.value, "value", None) is True for k in node.keywords):
                found.append(f"shell=True at line {node.lineno}")
        if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names) and filename not in SUBPROCESS_ALLOWED_IN:
            found.append("import subprocess")
    return found


def application_sources():
    skip = ("test_", "conftest", "weights_fixtures")
    return [p for p in BACKEND.glob("*.py") if not p.name.startswith(skip)]


def test_the_application_never_imports_a_deserialiser_or_evaluates_code():
    problems = {p.name: unsafe_constructs(p.read_text(encoding="utf-8"), p.name) for p in application_sources()}
    assert {k: v for k, v in problems.items() if v} == {}
    assert len(application_sources()) >= 15  # guards against the glob silently matching nothing


def test_the_checker_itself_catches_what_it_is_meant_to_catch():
    bad = "import pickle\nimport subprocess\nfrom joblib import load\nx = eval('1')\nos.system('x')\nsubprocess.run('x', shell=True)\nnp.load('f')\n"
    found = unsafe_constructs(bad, "other.py")
    for expected in ("import pickle", "import subprocess", "from joblib import ...", "eval()", "os.system()", "shell=True", "np.load()"):
        assert any(expected in f for f in found), (expected, found)
    assert unsafe_constructs("import pickletools\nimport json\n", "scan.py") == []  # the disassembler never executes


def test_pickletools_is_only_used_to_read_opcodes_never_to_load_them():
    source = (BACKEND / "scan.py").read_text(encoding="utf-8")
    used = set(re.findall(r"pickletools\.(\w+)", source))
    assert used == {"genops"}  # genops disassembles; there is no pickletools call that executes


# ------------------------------------------------------------------------- response headers

def test_responses_carry_protective_headers():
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_error_responses_carry_them_too():
    r = client.get("/weights/not-a-real-id")  # a 404
    assert r.status_code == 404 and r.headers["x-content-type-options"] == "nosniff"
    r = client.post("/weights", params={"name": "x", "filename": "m.pt"}, content=b"x")  # a 422
    assert r.status_code == 422 and r.headers["cache-control"] == "no-store"


def test_a_header_a_route_set_itself_is_not_overwritten():
    # Server-sent-event replies set their own cache-control; the middleware must leave it.
    from security import DEFAULT_HEADERS, SecurityHeadersMiddleware

    sent = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"cache-control", b"no-cache")]})
        await send({"type": "http.response.body", "body": b"x"})

    async def collect(message):
        sent.append(message)

    import asyncio

    asyncio.run(SecurityHeadersMiddleware(inner)({"type": "http", "path": "/x"}, None, collect))
    headers = dict(sent[0]["headers"])
    assert headers[b"cache-control"] == b"no-cache" and b"x-content-type-options" in headers
    assert len([h for h in sent[0]["headers"] if h[0] == b"cache-control"]) == 1
    assert set(DEFAULT_HEADERS) - {b"cache-control"} <= set(headers)


def test_the_interactive_docs_work_in_development_and_are_absent_in_production():
    assert client.get("/docs").status_code == 200
    assert "content-security-policy" not in client.get("/docs").headers  # the docs page needs outside scripts
    code = "import main; print(main.app.docs_url, main.app.redoc_url, main.app.openapi_url)"
    env = {**__import__("os").environ, "MODELHUB_ENV": "production"}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().splitlines()[-1] == "None None None", out.stderr[-300:]


# -------------------------------------------------------------------------- repository hygiene

def test_secrets_and_uploads_are_git_ignored():
    ignore = (BACKEND.parent / ".gitignore").read_text(encoding="utf-8")
    for needed in (".dev-secrets/", ".uploads/", ".env"):
        assert needed in ignore, needed

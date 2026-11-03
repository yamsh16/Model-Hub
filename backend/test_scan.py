"""The scanner must accept genuine files and reject malformed, disguised and hostile ones."""
import json
import struct

import pytest

import scan
from weights_fixtures import gguf_bytes, gguf_string, gguf_u32, safetensors_bytes


def scan_bytes(tmp_path, data, fmt, name="f"):
    path = tmp_path / name
    path.write_bytes(data)
    return scan.scan_file(str(path), fmt)


def rejected(tmp_path, data, fmt, match):
    with pytest.raises(scan.ScanError, match=match):
        scan_bytes(tmp_path, data, fmt)


# ---- genuine files, written by the reference libraries ----

def test_real_safetensors_file_is_accepted(tmp_path):
    np = pytest.importorskip("numpy")
    st = pytest.importorskip("safetensors.numpy")
    path = tmp_path / "real.safetensors"
    st.save_file(
        {"w": np.random.rand(4, 8).astype("float32"), "b": np.zeros(8, dtype="float16"), "i": np.arange(5, dtype="int64")},
        str(path), metadata={"format": "pt", "architecture": "toy"},
    )
    r = scan.scan_file(str(path), "safetensors")
    assert (r.tensor_count, r.parameter_count) == (3, 4 * 8 + 8 + 5)
    assert r.dtypes == {"F32": 1, "F16": 1, "I64": 1} and r.architecture == "toy"


def test_real_gguf_file_is_accepted_including_quantised_tensors(tmp_path):
    np = pytest.importorskip("numpy")
    gguf = pytest.importorskip("gguf")
    path = tmp_path / "real.gguf"
    w = gguf.GGUFWriter(str(path), "llama")
    w.add_name("tiny")
    w.add_tensor("tok_embd.weight", np.random.rand(16, 32).astype("float32"))
    w.add_tensor("blk.0.w", np.random.rand(8, 32).astype("float16"))
    q = gguf.quants.quantize(np.random.rand(4, 64).astype("float32"), gguf.GGMLQuantizationType.Q8_0)
    w.add_tensor("blk.0.q", q, raw_dtype=gguf.GGMLQuantizationType.Q8_0)
    w.add_array("tokenizer.ggml.tokens", ["a", "b", "c"])
    w.add_string("tokenizer.chat_template", "{{ messages }}")
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    r = scan.scan_file(str(path), "gguf")
    assert r.architecture == "llama" and r.tensor_count == 3
    assert r.dtypes == {"F32": 1, "F16": 1, "Q8_0": 1}
    assert any("chat template" in w for w in r.warnings)


# ---- hand-built files ----

def test_minimal_valid_files(tmp_path):
    r = scan_bytes(tmp_path, safetensors_bytes(metadata={"architecture": "x"}), "safetensors")
    assert r.tensor_count == 1 and r.parameter_count == 4
    r = scan_bytes(tmp_path, gguf_bytes(), "gguf")
    assert r.tensor_count == 1 and r.parameter_count == 32 and r.architecture == "llama"


def test_extension_mapping():
    assert scan.format_for_filename("Model.SafeTensors") == "safetensors"
    assert scan.format_for_filename("x.gguf") == "gguf"
    for bad in ("m.pt", "m.pkl", "m.bin", "m.ckpt", "m.joblib", "m.onnx", "m", "m.safetensors.pt"):
        with pytest.raises(scan.ScanError, match="Only .safetensors and .gguf"):
            scan.format_for_filename(bad)


# ---- code-bearing formats renamed to look harmless ----

@pytest.mark.parametrize(
    "head,what",
    [
        (b"PK\x03\x04" + b"\0" * 40, "ZIP"),  # a .pt / .pth file
        (b"\x80\x02" + b"}q\x00." + b"\0" * 20, "pickle"),
        (b"\x80\x05\x95" + b"\0" * 20, "pickle"),
        (b"\x89HDF\r\n\x1a\n" + b"\0" * 20, "HDF5"),
    ],
)
@pytest.mark.parametrize("fmt", ["safetensors", "gguf"])
def test_disguised_unsafe_formats_are_recognised(tmp_path, head, what, fmt):
    rejected(tmp_path, head, fmt, what)


def test_mismatched_content_gets_a_helpful_message(tmp_path):
    rejected(tmp_path, safetensors_bytes(), "gguf", "looks like safetensors")
    rejected(tmp_path, gguf_bytes(), "safetensors", "content is GGUF")
    rejected(tmp_path, b"", "gguf", "empty") if False else None
    with pytest.raises(scan.ScanError, match="empty"):
        scan_bytes(tmp_path, b"", "gguf")


# ---- malformed or hostile safetensors ----

@pytest.mark.parametrize(
    "build,match",
    [
        (lambda: b"\x01\x02", "too small"),
        (lambda: struct.pack("<Q", 2**63) + b"{}", "implausibly large"),
        (lambda: struct.pack("<Q", 500) + b"{}", "longer than the file"),
        (lambda: struct.pack("<Q", 2) + b"[]", "does not start with"),
        (lambda: struct.pack("<Q", 5) + b"{nope", "not valid JSON"),
        (lambda: struct.pack("<Q", 7) + b'{"a":1}', "tensor 'a' must have exactly"),
        (lambda: safetensors_bytes(truncate=3), "truncated"),
        (lambda: safetensors_bytes(trailing=b"EXTRA"), "extra bytes"),
        (lambda: safetensors_bytes({"w": ("F32", [2, 2], b"\0" * 12)}), "do not match"),
        (lambda: safetensors_bytes({"w": ("F32", [-1, 2], b"")}), "invalid shape"),
        (lambda: safetensors_bytes({"w": ("F32", [1.5], b"\0" * 4)}), "invalid shape"),
        (lambda: safetensors_bytes({"w": ("F32", [True], b"\0" * 4)}), "invalid shape"),
        (lambda: safetensors_bytes({"w": (7, [1], b"\0" * 4)}), "non-string dtype"),
        (lambda: safetensors_bytes(metadata={"k": 5}), "strings to strings"),
        (lambda: safetensors_bytes(header_override=lambda h: {**h, "w": {**h["w"], "data_offsets": [8, 4]}}), "invalid data_offsets"),
        (lambda: safetensors_bytes(header_override=lambda h: {**h, "w": {**h["w"], "extra": 1}}), "exactly dtype"),
    ],
)
def test_malformed_safetensors_are_rejected(tmp_path, build, match):
    rejected(tmp_path, build(), "safetensors", match)


def test_safetensors_overlapping_or_gapped_tensors_are_rejected(tmp_path):
    two = {"a": ("F32", [1], b"\0" * 4), "b": ("F32", [1], b"\0" * 4)}
    overlap = safetensors_bytes(two, header_override=lambda h: {**h, "b": {**h["b"], "data_offsets": [2, 6]}})
    rejected(tmp_path, overlap, "safetensors", "overlap or leave gaps")
    gap = safetensors_bytes(two, header_override=lambda h: {**h, "b": {**h["b"], "data_offsets": [8, 12]}}, trailing=b"\0" * 4)
    rejected(tmp_path, gap, "safetensors", "overlap or leave gaps")


def test_safetensors_duplicate_header_keys_are_rejected(tmp_path):
    # Two parsers could disagree about which duplicate wins; refuse the ambiguity.
    raw = b'{"w":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},"w":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}}'
    rejected(tmp_path, struct.pack("<Q", len(raw)) + raw + b"\0" * 4, "safetensors", "repeats the key")


def test_safetensors_unknown_dtype_is_allowed_with_a_warning(tmp_path):
    r = scan_bytes(tmp_path, safetensors_bytes({"w": ("F4_FUTURE", [2], b"\0")}), "safetensors")
    assert any("Unrecognised dtype" in w for w in r.warnings)


def test_safetensors_deeply_nested_json_does_not_crash(tmp_path):
    raw = b"{" + b'"a":' * 5000 + b"1" + b"}" * 1  # unbalanced and deep
    rejected(tmp_path, struct.pack("<Q", len(raw)) + raw, "safetensors", "not valid JSON")
    nested = b'{"a":' + b"[" * 100000 + b"]" * 100000 + b"}"
    rejected(tmp_path, struct.pack("<Q", len(nested)) + nested + b"", "safetensors", "JSON|tensor")


def test_safetensors_huge_shape_numbers_do_not_overflow(tmp_path):
    big = safetensors_bytes({"w": ("F32", [2**62, 2**62], b"\0" * 4)})
    rejected(tmp_path, big, "safetensors", "do not match")


# ---- malformed or hostile GGUF ----

@pytest.mark.parametrize(
    "build,match",
    [
        (lambda: b"GGU", "not GGUF"),
        (lambda: b"GGUF" + struct.pack("<I", 1) + b"\0" * 16, "Unsupported GGUF version 1"),
        (lambda: b"GGUF" + struct.pack(">I", 3) + b"\0" * 16, "Big-endian"),
        (lambda: gguf_bytes(version=99), "Unsupported GGUF version"),
        (lambda: gguf_bytes(tensor_count=2**40), "implausible number"),
        (lambda: gguf_bytes(kv_count=2**40), "implausible number"),
        (lambda: gguf_bytes(kv_count=5), "GGUF"),  # claims more entries than exist: misparses, must still reject
        (lambda: gguf_bytes(kvs=[("k", 8, struct.pack("<Q", 2**62))]), "implausibly long string"),
        (lambda: gguf_bytes(kvs=[("k", 8, struct.pack("<Q", 10_000_000))]), "past the end"),
        (lambda: gguf_bytes(kvs=[("k", 77, b"")]), "unknown value type"),
        (lambda: gguf_bytes(kvs=[("k", 9, struct.pack("<IQ", 4, 2**60))]), "past the end"),
        (lambda: gguf_bytes(kvs=[("k", 9, struct.pack("<IQ", 8, 2**40))]), "more strings than"),
        (lambda: gguf_bytes(kvs=[("k", 9, struct.pack("<IQ", 9, 1))]), "unknown element type"),
        (lambda: gguf_bytes(kvs=[("a", 4, gguf_u32(1)), ("a", 4, gguf_u32(2))]), "repeats the metadata key"),
        (lambda: gguf_bytes(kvs=[("general.alignment", 4, gguf_u32(48))]), "power of two"),
        (lambda: gguf_bytes(kvs=[("general.alignment", 4, gguf_u32(0))]), "power of two"),
        (lambda: gguf_bytes(kvs=[("general.alignment", 5, gguf_u32(32))]), "uint32"),
        (lambda: gguf_bytes(tensors=[("w", [4, 8], 0, 0), ("w", [4, 8], 0, 0)]), "two tensors named"),
        (lambda: gguf_bytes(tensors=[("w", [], 0, 0)]), "dimensions"),
        (lambda: gguf_bytes(tensors=[("w", [1, 1, 1, 1, 1], 0, 0)]), "dimensions"),
        (lambda: gguf_bytes(tensors=[("w", [4, 8], 0, 32)]), "extends beyond"),
        (lambda: gguf_bytes(tensors=[("w", [4, 8], 0, 5)]), "not aligned"),
        (lambda: gguf_bytes(tensors=[("w", [4, 8], 0, 10**12)]), "not aligned|extends beyond"),
        (lambda: gguf_bytes(tensors=[("w", [4, 8], 8, 0)]), "block size"),  # Q8_0 needs a multiple of 32
        (lambda: gguf_bytes(tensors=[("w", [2**40, 2**40], 0, 0)]), "extends beyond"),
        (lambda: gguf_bytes(tensors=[("w", [4, 8], 123456, 0)]), "invalid type"),
        (lambda: gguf_bytes(tensors=[("w", [4, 8], 0, 0)], data_len=10), "extends beyond"),
    ],
)
def test_malformed_gguf_is_rejected(tmp_path, build, match):
    rejected(tmp_path, build(), "gguf", match)


def test_gguf_unknown_tensor_type_only_needs_an_in_range_offset(tmp_path):
    ok = gguf_bytes(tensors=[("w", [4, 8], 34, 0)])  # a type id this scanner has no layout for
    assert scan_bytes(tmp_path, ok, "gguf").dtypes == {"type_34": 1}
    rejected(tmp_path, gguf_bytes(tensors=[("w", [4, 8], 34, 32 * 100)]), "gguf", "beyond the end")


def test_gguf_custom_alignment_is_honoured(tmp_path):
    kvs = [("general.alignment", 4, gguf_u32(64))]
    ok = gguf_bytes(kvs=kvs, tensors=[("w", [4, 8], 0, 0)], alignment=64)
    assert scan_bytes(tmp_path, ok, "gguf").tensor_count == 1
    rejected(tmp_path, gguf_bytes(kvs=kvs, tensors=[("w", [4, 8], 0, 32)], alignment=64), "gguf", "aligned to 64")


def test_gguf_reading_never_goes_past_a_small_file(tmp_path):
    """Every prefix of a valid file must be rejected cleanly, never crash or hang."""
    full = gguf_bytes(kvs=[("general.architecture", 8, gguf_string("llama")), ("a", 9, struct.pack("<IQ", 8, 2) + gguf_string("x") + gguf_string("y"))])
    for cut in range(0, len(full) - 1, 3):
        with pytest.raises(scan.ScanError):
            scan_bytes(tmp_path, full[:cut], "gguf")


def test_safetensors_every_prefix_is_rejected_cleanly(tmp_path):
    full = safetensors_bytes({"a": ("F32", [2], b"\0" * 8), "b": ("U8", [3], b"\0" * 3)})
    for cut in range(1, len(full)):
        with pytest.raises(scan.ScanError):
            scan_bytes(tmp_path, full[:cut], "safetensors")


def test_scan_reports_are_json_serialisable(tmp_path):
    from dataclasses import asdict

    r = scan_bytes(tmp_path, gguf_bytes(), "gguf")
    json.dumps(asdict(r))


@pytest.mark.parametrize("fmt", ["safetensors", "gguf"])
def test_random_corruption_never_crashes_the_scanner(tmp_path, fmt):
    """Flip bytes in valid files at random: the only outcomes allowed are 'accepted' or ScanError.
    Anything else (an IndexError, MemoryError, struct.error, a hang) would be a bug an upload could trigger."""
    import random

    rng = random.Random(1234)
    base = (
        safetensors_bytes({"a": ("F32", [2, 2], b"\0" * 16), "b": ("U8", [3], b"\0" * 3)}, metadata={"k": "v"})
        if fmt == "safetensors"
        else gguf_bytes(kvs=[("general.architecture", 8, gguf_string("llama")),
                             ("t", 9, struct.pack("<IQ", 8, 2) + gguf_string("x") + gguf_string("y"))])
    )
    path = tmp_path / "fuzz"
    for _ in range(400):
        data = bytearray(base)
        for _ in range(rng.randint(1, 4)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.2:
            data = data[: rng.randrange(1, len(data))]
        path.write_bytes(bytes(data))
        try:
            scan.scan_file(str(path), fmt)
        except scan.ScanError:
            pass

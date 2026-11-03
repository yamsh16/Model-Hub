"""Builders for weight files used by the scanner and upload tests (hand-rolled so tests can
make deliberately broken files; the real libraries are used separately to prove that genuine
files are accepted)."""
import json
import struct


def safetensors_bytes(tensors=None, metadata=None, header_override=None, trailing=b"", truncate=0):
    """tensors: {name: (dtype, shape, raw_bytes)}. Offsets are laid out contiguously."""
    tensors = tensors if tensors is not None else {"w": ("F32", [2, 2], b"\0" * 16)}
    header, data, cursor = {}, b"", 0
    for name, (dtype, shape, raw) in tensors.items():
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [cursor, cursor + len(raw)]}
        data += raw
        cursor += len(raw)
    if metadata is not None:
        header["__metadata__"] = metadata
    if header_override is not None:
        header = header_override(header)
    raw_header = header if isinstance(header, bytes) else json.dumps(header).encode()
    blob = struct.pack("<Q", len(raw_header)) + raw_header + data + trailing
    return blob[: len(blob) - truncate] if truncate else blob


def _s(text):
    raw = text.encode()
    return struct.pack("<Q", len(raw)) + raw


def gguf_bytes(kvs=None, tensors=None, version=3, alignment=32, data_len=None, tensor_count=None,
               kv_count=None, pad_data=True):
    """kvs: list of (key, type_id, packed_value_bytes). tensors: list of (name, dims, ggml_type, offset)."""
    kvs = kvs if kvs is not None else [("general.architecture", 8, _s("llama"))]
    tensors = tensors if tensors is not None else [("w", [4, 8], 0, 0)]  # 32 f32 = 128 bytes
    out = b"GGUF" + struct.pack("<I", version)
    out += struct.pack("<QQ", len(tensors) if tensor_count is None else tensor_count,
                       len(kvs) if kv_count is None else kv_count)
    for key, vtype, value in kvs:
        out += _s(key) + struct.pack("<I", vtype) + value
    for name, dims, ggml_type, offset in tensors:
        out += _s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<IQ", ggml_type, offset)
    if pad_data:
        out += b"\0" * (-len(out) % alignment)
    out += b"\0" * (128 if data_len is None else data_len)
    return out


def gguf_string(text):
    return _s(text)


def gguf_u32(n):
    return struct.pack("<I", n)

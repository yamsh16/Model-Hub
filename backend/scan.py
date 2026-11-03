"""Structural scanner for uploaded model weight files (safetensors and GGUF only).

Uploaded files are untrusted. Nothing here loads, executes or deserialises tensor data: the
scanner reads only the header region, with every length bounded by the file size, and checks
that the file is what it claims to be and is internally consistent. A file that passes is
still not "safe to run" (see the warnings, and the sandboxed serving that comes later); a file
that fails is rejected with the reason.

Why these formats: safetensors and GGUF are data-only by design. Pickle-based formats
(.pkl/.pt/.pth/.bin/.ckpt/.joblib) run code when loaded, so they are never accepted; a
renamed one is recognised by its first bytes.
"""
from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from typing import BinaryIO

ALLOWED_EXTENSIONS = {".safetensors": "safetensors", ".gguf": "gguf"}


class ScanError(Exception):
    """The file is not an acceptable weights file; the message says why (shown to the user)."""


@dataclass
class ScanResult:
    format: str
    tensor_count: int
    parameter_count: int
    dtypes: dict[str, int]  # dtype name -> number of tensors
    architecture: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)  # a small, safe subset
    warnings: list[str] = field(default_factory=list)


def format_for_filename(filename: str) -> str:
    ext = os.path.splitext(filename.lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        raise ScanError(
            f"Only .safetensors and .gguf files are accepted (got '{ext or 'no extension'}'). "
            "Pickle-based formats such as .pt, .pkl and .bin can run code when loaded."
        )
    return ALLOWED_EXTENSIONS[ext]


# ---------------------------------------------------------------- recognising unsafe files

def sniff_unsafe(head: bytes) -> str | None:
    """Name the dangerous format a file really is, judged by its first bytes, or None."""
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "a ZIP archive (PyTorch .pt/.pth files are zips containing pickled code)"
    if len(head) >= 2 and head[0] == 0x80 and 2 <= head[1] <= 5:
        return "a Python pickle, which runs code when loaded"
    if head[:8] == b"\x89HDF\r\n\x1a\n":
        return "an HDF5 file (Keras .h5 models can embed code)"
    if head[:2] in (b"\x1f\x8b",):
        return "a gzip archive"
    return None


# ------------------------------------------------------------------------------ safetensors

# bytes per element; sub-byte and newer types are accepted but not size-checked
_ST_DTYPE_BYTES = {
    "BOOL": 1, "U8": 1, "I8": 1, "F8_E5M2": 1, "F8_E4M3": 1, "F8_E8M0": 1,
    "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
    "I32": 4, "U32": 4, "F32": 4,
    "I64": 8, "U64": 8, "F64": 8,
}
_ST_MAX_HEADER = 100_000_000  # the format's own limit
_ST_MAX_TENSORS = 1_000_000
_ST_MAX_DIMS = 64


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ScanError(f"safetensors header repeats the key '{key[:60]}' (parsers could disagree about it)")
        seen[key] = value
    return seen


def _scan_safetensors(f: BinaryIO, size: int) -> ScanResult:
    if size < 8:
        raise ScanError("File is too small to be a safetensors file")
    (header_len,) = struct.unpack("<Q", f.read(8))
    if header_len > _ST_MAX_HEADER:
        raise ScanError("safetensors header length is implausibly large")
    if 8 + header_len > size:
        raise ScanError("safetensors header is longer than the file (truncated or not a safetensors file)")
    raw = f.read(header_len)
    if not raw.startswith(b"{"):
        raise ScanError("safetensors header does not start with '{' (not a safetensors file)")
    try:
        header = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ScanError("safetensors header is not valid JSON")
    if not isinstance(header, dict):
        raise ScanError("safetensors header must be a JSON object")

    result = ScanResult(format="safetensors", tensor_count=0, parameter_count=0, dtypes={})
    meta = header.pop("__metadata__", None)
    if meta is not None:
        if not isinstance(meta, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in meta.items()):
            raise ScanError("safetensors __metadata__ must map strings to strings")
        result.metadata = {k[:60]: v[:200] for k, v in list(meta.items())[:20]}
        result.architecture = meta.get("architecture") or meta.get("modelspec.architecture")

    if len(header) > _ST_MAX_TENSORS:
        raise ScanError("safetensors header lists too many tensors")

    data_size = size - 8 - header_len
    spans = []
    for name, info in header.items():
        if not isinstance(info, dict) or set(info) != {"dtype", "shape", "data_offsets"}:
            raise ScanError(f"tensor '{name[:60]}' must have exactly dtype, shape and data_offsets")
        dtype, shape, offsets = info["dtype"], info["shape"], info["data_offsets"]
        if not isinstance(dtype, str):
            raise ScanError(f"tensor '{name[:60]}' has a non-string dtype")
        if (
            not isinstance(shape, list) or len(shape) > _ST_MAX_DIMS
            or not all(type(d) is int and d >= 0 for d in shape)
        ):
            raise ScanError(f"tensor '{name[:60]}' has an invalid shape")
        if (
            not isinstance(offsets, list) or len(offsets) != 2
            or not all(type(o) is int and o >= 0 for o in offsets) or offsets[0] > offsets[1]
        ):
            raise ScanError(f"tensor '{name[:60]}' has invalid data_offsets")
        elements = 1
        for d in shape:
            elements *= d
        width = _ST_DTYPE_BYTES.get(dtype)
        if width is None:
            result.warnings.append(f"Unrecognised dtype '{dtype[:30]}'; its size was not checked")
        elif elements * width != offsets[1] - offsets[0]:
            raise ScanError(f"tensor '{name[:60]}': shape and dtype do not match its data size")
        spans.append((offsets[0], offsets[1]))
        result.dtypes[dtype] = result.dtypes.get(dtype, 0) + 1
        result.parameter_count += elements
    result.tensor_count = len(spans)

    # The data section must be tiled exactly by the tensors: no gaps, overlaps or trailing bytes.
    cursor = 0
    for begin, end in sorted(spans):
        if begin != cursor:
            raise ScanError("safetensors tensors overlap or leave gaps in the data section")
        cursor = end
    if cursor != data_size:
        raise ScanError(
            "safetensors data section size does not match the header "
            + ("(file is truncated)" if cursor > data_size else "(extra bytes after the last tensor)")
        )
    return result


# ------------------------------------------------------------------------------------ GGUF

_GGUF_MAX_TENSORS = 1_000_000
_GGUF_MAX_KV = 100_000
_GGUF_MAX_STRING = 16 * 1024 * 1024
_GGUF_MAX_DIMS = 4
_GGUF_DEFAULT_ALIGNMENT = 32

# value type -> (struct format, size) for fixed-size scalars
_GGUF_SCALARS = {
    0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2), 4: ("<I", 4), 5: ("<i", 4),
    6: ("<f", 4), 7: ("<?", 1), 10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8),
}
_GGUF_STRING, _GGUF_ARRAY = 8, 9

# ggml type id -> (name, elements per block, bytes per block). Only types whose layout is
# certain are listed; others are accepted with just a bounds check on their offset.
_GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22), 7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110), 12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    24: ("I8", 1, 1), 25: ("I16", 1, 2), 26: ("I32", 1, 4), 27: ("I64", 1, 8),
    28: ("F64", 1, 8), 30: ("BF16", 1, 2),
}
_GGUF_KEEP_KEYS = ("general.name", "general.architecture", "general.file_type", "general.size_label",
                   "general.license", "general.quantization_version", "general.basename")


class _Reader:
    """Reads from the header region and refuses to go past the end of the file."""

    def __init__(self, f: BinaryIO, size: int):
        self.f, self.size = f, size

    @property
    def pos(self) -> int:
        return self.f.tell()

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > self.size:
            raise ScanError("GGUF header runs past the end of the file (truncated or corrupt)")
        data = self.f.read(n)
        if len(data) != n:
            raise ScanError("GGUF header runs past the end of the file (truncated or corrupt)")
        return data

    def skip(self, n: int) -> None:
        if n < 0 or self.pos + n > self.size:
            raise ScanError("GGUF header runs past the end of the file (truncated or corrupt)")
        self.f.seek(n, os.SEEK_CUR)

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def string(self, keep: bool = True) -> str | None:
        n = self.u64()
        if n > _GGUF_MAX_STRING:
            raise ScanError("GGUF contains an implausibly long string")
        if not keep:
            self.skip(n)
            return None
        try:
            return self.take(n).decode("utf-8")
        except UnicodeDecodeError:
            raise ScanError("GGUF contains a string that is not valid UTF-8")


def _skip_value(r: _Reader, vtype: int, depth: int = 0) -> None:
    if vtype in _GGUF_SCALARS:
        r.skip(_GGUF_SCALARS[vtype][1])
    elif vtype == _GGUF_STRING:
        r.string(keep=False)
    elif vtype == _GGUF_ARRAY:
        if depth >= 1:
            raise ScanError("GGUF has nested arrays, which the format does not allow")
        etype, count = r.u32(), r.u64()
        if etype in _GGUF_SCALARS:
            r.skip(count * _GGUF_SCALARS[etype][1])  # bounds-checked: cannot overrun the file
        elif etype in (_GGUF_STRING,):
            if count * 8 > r.size - r.pos:  # each string needs at least its 8-byte length
                raise ScanError("GGUF array claims more strings than the file can hold")
            for _ in range(count):
                r.string(keep=False)
        else:
            raise ScanError(f"GGUF array has an unknown element type {etype}")
    else:
        raise ScanError(f"GGUF metadata has an unknown value type {vtype}")


def _scan_gguf(f: BinaryIO, size: int) -> ScanResult:
    r = _Reader(f, size)
    if r.take(4) != b"GGUF":
        raise ScanError("Not a GGUF file (missing the GGUF signature)")
    version = r.u32()
    if version in (0x01000000, 0x02000000, 0x03000000):
        raise ScanError("Big-endian GGUF files are not supported")
    if version not in (2, 3):
        raise ScanError(f"Unsupported GGUF version {version} (accepted: 2 and 3)")
    tensor_count, kv_count = r.u64(), r.u64()
    if tensor_count > _GGUF_MAX_TENSORS or kv_count > _GGUF_MAX_KV:
        raise ScanError("GGUF header declares an implausible number of tensors or metadata entries")

    result = ScanResult(format="gguf", tensor_count=0, parameter_count=0, dtypes={})
    alignment = _GGUF_DEFAULT_ALIGNMENT
    seen_keys = set()
    for _ in range(kv_count):
        key = r.string()
        if key in seen_keys:
            raise ScanError(f"GGUF repeats the metadata key '{key[:60]}'")
        seen_keys.add(key)
        vtype = r.u32()
        if key == "tokenizer.chat_template":
            result.warnings.append(
                "Contains a chat template (a Jinja program). It must only ever be rendered in a "
                "sandbox: loaders that render it unsandboxed have had code-execution bugs."
            )
        if key in _GGUF_KEEP_KEYS and vtype == _GGUF_STRING:
            value = r.string()
            result.metadata[key] = value[:200]
            if key == "general.architecture":
                result.architecture = value[:100]
        elif key in _GGUF_KEEP_KEYS and vtype in _GGUF_SCALARS:
            fmt, width = _GGUF_SCALARS[vtype]
            value = struct.unpack(fmt, r.take(width))[0]
            result.metadata[key] = str(value)
        elif key == "general.alignment":
            if vtype != 4:
                raise ScanError("GGUF general.alignment must be a uint32")
            alignment = r.u32()
            if alignment == 0 or alignment & (alignment - 1) or alignment > 1 << 20:
                raise ScanError("GGUF general.alignment must be a power of two")
        else:
            _skip_value(r, vtype)

    tensors = []
    names = set()
    for _ in range(tensor_count):
        name = r.string()
        if name in names:
            raise ScanError(f"GGUF has two tensors named '{name[:60]}'")
        names.add(name)
        n_dims = r.u32()
        if not 1 <= n_dims <= _GGUF_MAX_DIMS:
            raise ScanError(f"Tensor '{name[:60]}' has {n_dims} dimensions (1 to {_GGUF_MAX_DIMS} allowed)")
        dims = [r.u64() for _ in range(n_dims)]
        ggml_type, offset = r.u32(), r.u64()
        tensors.append((name, dims, ggml_type, offset))

    data_start = (r.pos + alignment - 1) // alignment * alignment
    if data_start > size:
        raise ScanError("GGUF has no tensor data section (truncated)")
    data_size = size - data_start

    for name, dims, ggml_type, offset in tensors:
        elements = 1
        for d in dims:
            elements *= d
        if offset % alignment:
            raise ScanError(f"Tensor '{name[:60]}' is not aligned to {alignment} bytes")
        spec = _GGML_TYPES.get(ggml_type)
        if spec is None:
            if ggml_type > 1000:
                raise ScanError(f"Tensor '{name[:60]}' has an invalid type {ggml_type}")
            if offset > data_size:
                raise ScanError(f"Tensor '{name[:60]}' starts beyond the end of the file")
            type_name = f"type_{ggml_type}"
        else:
            type_name, block, block_bytes = spec
            if dims[0] % block:
                raise ScanError(f"Tensor '{name[:60]}': first dimension is not a multiple of the {type_name} block size")
            if offset + elements // block * block_bytes > data_size:
                raise ScanError(f"Tensor '{name[:60]}' extends beyond the end of the file (truncated)")
        result.dtypes[type_name] = result.dtypes.get(type_name, 0) + 1
        result.parameter_count += elements
    result.tensor_count = len(tensors)
    return result


# --------------------------------------------------------------------------------- entry point

_SCANNERS = {"safetensors": _scan_safetensors, "gguf": _scan_gguf}


def scan_file(path: str, fmt: str) -> ScanResult:
    """Scan the file at `path` as format `fmt`; raise ScanError if it is not acceptable."""
    size = os.path.getsize(path)
    if size == 0:
        raise ScanError("The file is empty")
    with open(path, "rb") as f:
        head = f.read(16)
        unsafe = sniff_unsafe(head)
        if unsafe:
            raise ScanError(f"Rejected: this file is {unsafe}. Only safetensors and GGUF are accepted.")
        f.seek(0)
        if fmt == "gguf" and head[:4] != b"GGUF":
            hint = " (it looks like safetensors; rename it to .safetensors)" if _looks_like_safetensors(head, size) else ""
            raise ScanError(f"The content is not GGUF{hint}")
        if fmt == "safetensors" and head[:4] == b"GGUF":
            raise ScanError("The content is GGUF, not safetensors (rename it to .gguf)")
        return _SCANNERS[fmt](f, size)


def _looks_like_safetensors(head: bytes, size: int) -> bool:
    return len(head) >= 9 and head[8:9] == b"{" and struct.unpack("<Q", head[:8])[0] + 8 <= size

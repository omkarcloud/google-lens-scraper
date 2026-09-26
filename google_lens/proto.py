"""Protobuf helpers for the two binary Lens surfaces (no .proto files, no
protoc, no dependency). The wire codec is snapchat/proto.py's; this module
adds float32 fields (Lens geometry is all `float`) and a typed reader.

Message / field numbers come from Chromium's lens_overlay_*.proto
(components/lens/proto), read 2026-09-26 off the descriptors bundled with
the chrome-lens-py client; only the fields this scraper touches are listed
where they are used (google_lens/lens_api.py, google_lens/parsers.py).

    encode(fields)   snapchat's encoder + ("f32", x) values
    message(data)    bytes -> Msg (typed accessors over snapchat's decode)
    b64url(raw) / unb64(text)
"""
import base64
import struct

from snapchat.proto import _field, _varint, decode as _decode


def encode(fields):
    """{field_number: value | [values]} -> bytes. int -> varint, str/bytes ->
    length-delimited, dict -> nested message, ("f32", x) -> float32,
    None -> skipped. Field order follows the dict's insertion order (the
    server does not care, and this keeps the request readable)."""
    out = bytearray()
    for number, values in fields.items():
        if not isinstance(values, list):
            values = [values]
        for value in values:
            if value is None:
                continue
            if isinstance(value, tuple) and value[0] == "f32":
                out += _field(number, 5, struct.pack("<f", float(value[1])))
            elif isinstance(value, bool):
                out += _field(number, 0, _varint(int(value)))
            elif isinstance(value, int):
                out += _field(number, 0, _varint(value))
            elif isinstance(value, dict):
                body = encode(value)
                out += _field(number, 2, _varint(len(body)) + body)
            else:
                if isinstance(value, str):
                    value = value.encode("utf-8")
                out += _field(number, 2, _varint(len(value)) + bytes(value))
    return bytes(out)


class Msg:
    """Typed, never-raising view over one decoded message: every accessor
    returns None / [] when the field is absent or has another wire type."""

    __slots__ = ("_f",)

    def __init__(self, data):
        try:
            self._f = _decode(data or b"")
        except (ValueError, IndexError):
            self._f = {}

    def _raw(self, number, wire):
        return [value for w, value in self._f.get(number, []) if w == wire]

    def has(self, number):
        return number in self._f

    def int(self, number):
        values = self._raw(number, 0)
        return values[0] if values else None

    def sint64(self, number):
        """int64 fields carry negatives as two's complement varints."""
        value = self.int(number)
        if value is not None and value >= 1 << 63:
            value -= 1 << 64
        return value

    def float(self, number):
        values = self._raw(number, 5)
        return struct.unpack("<f", values[0].to_bytes(4, "little"))[0] if values else None

    def double(self, number):
        values = self._raw(number, 1)
        return struct.unpack("<d", values[0].to_bytes(8, "little"))[0] if values else None

    def bytes(self, number):
        values = self._raw(number, 2)
        return values[0] if values else None

    def str(self, number):
        raw = self.bytes(number)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def strs(self, number):
        out = []
        for raw in self._raw(number, 2):
            try:
                out.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                pass
        return out

    def msg(self, number):
        raw = self.bytes(number)
        return Msg(raw) if raw is not None else None

    def msgs(self, number):
        return [Msg(raw) for raw in self._raw(number, 2)]


def message(data):
    return Msg(data)


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64(text):
    text = (text or "").strip().replace("+", "-").replace("/", "_")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

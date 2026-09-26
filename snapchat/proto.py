"""A minimal protobuf wire codec for the ONE binary surface this scraper
touches: Spotlight comments (`POST https://www.snapchat.com/getreplies`,
content-type application/x-protobuf). No .proto files, no protoc, no
dependency — the two messages are small and their field numbers were read
off the web app's bundle (2026-09-22).

    encode(fields)  {field_number: value | [values]} -> bytes
                    int -> varint, bytes/str -> length-delimited,
                    dict -> nested message, ("fixed64", int) -> fixed64
    decode(data)    bytes -> {field_number: [(wire_type, raw), ...]}
                    raw is an int for varints / fixed64, bytes otherwise;
                    the caller knows which fields are strings or messages.

GetRepliesRequest
  1 metadata      RequestMetadata
      1 requestId          string (uuid4)
      2 requestTimestampMs int64
      3 origin             enum, 20 = SNAPWEB
      4 clientInfo         { 1 userId Uuid{1 fixed64 high, 2 fixed64 low},
                             6 countryCode string }
  2 snapId        string (the spotlight snap id)
  5 approvalState enum, 3 = APPROVED (what the site lists)
  7 paginationCursor bytes (from the previous response)
  8 parentCommentId  Uuid — set to list a comment's threaded replies
  9 consistencyRequested enum 2
  11 textEncoding enum 0

GetRepliesResponse
  1 requestId string
  2 replies   repeated Reply (see parsers.comment for the fields)
  4 paginationCursor bytes (absent when exhausted)
"""


def _varint(value):
    value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(data, pos):
    result = shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def _field(number, wire_type, payload):
    return _varint((number << 3) | wire_type) + payload


def encode(fields):
    """{field_number: value} -> protobuf bytes. Lists encode as repeated
    fields; None values are skipped."""
    out = bytearray()
    for number in sorted(fields):
        values = fields[number]
        if not isinstance(values, list):
            values = [values]
        for value in values:
            if value is None:
                continue
            if isinstance(value, tuple) and len(value) == 2 and value[0] == "fixed64":
                out += _field(number, 1, (value[1] & ((1 << 64) - 1)).to_bytes(8, "little"))
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


def decode(data):
    """bytes -> {field_number: [(wire_type, raw), ...]} in wire order."""
    fields = {}
    pos = 0
    data = bytes(data)
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, pos = _read_varint(data, pos)
        elif wire_type == 1:
            value = int.from_bytes(data[pos:pos + 8], "little")
            pos += 8
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        elif wire_type == 5:
            value = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        if pos > len(data):
            raise ValueError(f"truncated field {number}")
        fields.setdefault(number, []).append((wire_type, value))
    return fields


def first(fields, number, default=None):
    values = fields.get(number)
    return values[0][1] if values else default


def text(fields, number):
    raw = first(fields, number)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except UnicodeDecodeError:
        return None


def message(fields, number):
    raw = first(fields, number)
    return decode(raw) if isinstance(raw, (bytes, bytearray)) else None


def messages(fields, number):
    return [decode(raw) for wire, raw in fields.get(number, []) if isinstance(raw, (bytes, bytearray))]


def uuid_pair(fields):
    """A Snap 128-bit id message {1 fixed64 high, 2 fixed64 low} ->
    "high-low" (both as unsigned decimal strings, the JSON form the pages
    use), or None."""
    if not fields:
        return None
    high, low = first(fields, 1), first(fields, 2)
    if high is None or low is None:
        return None
    return f"{high}-{low}"


def uuid_message(value):
    """"high-low" -> the {1 fixed64, 2 fixed64} message for encode()."""
    high, _, low = str(value).partition("-")
    return {1: ("fixed64", int(high)), 2: ("fixed64", int(low))}

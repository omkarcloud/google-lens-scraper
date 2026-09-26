"""The streamed answer of /async/folsrch (the Lens AI overview): a
sequence of frames, each `TYPE;NAME<hex length>;VALUE` (verified
2026-09-26):

    2;md<n>;[[["KEY","<json>"],…]]         jsdata for the fragments
    0;<n>;<html>                           HTML appended to the current target
    b;placeholder<n>;<id>                  an empty slot named <id> in the current target
    18;write_to_target_document<n>;<id>    later HTML goes to slot <id> ("default" = the page)

Lengths count UTF-16 code units (an emoji is two), so the parser walks the
UTF-16 encoding. assemble() rebuilds the final HTML by filling every slot
with what was written to it; jsdata() returns the md pairs.
"""
import json
import re

_HEADER_RE = re.compile(r"([0-9a-z]{1,3});(md|placeholder|write_to_target_document)?([0-9a-f]{1,8});")
_SLOT = "\x00SLOT:{}\x00"
_SLOT_RE = re.compile(r"\x00SLOT:([^\x00]+)\x00")


def frames(text):
    """[(type, name, value)] in stream order; stops at the first byte that
    is not a frame header (a truncated stream keeps what came before)."""
    raw = (text or "").encode("utf-16-le", "surrogatepass")
    units = len(raw) // 2
    pos, out = 0, []
    while pos < units:
        head = raw[pos * 2: min(units, pos + 64) * 2].decode("utf-16-le", "ignore")
        m = _HEADER_RE.match(head)
        if not m:
            break
        start = pos + m.end()
        size = int(m.group(3), 16)
        value = raw[start * 2: (start + size) * 2].decode("utf-16-le", "surrogatepass")
        out.append((m.group(1), m.group(2) or "", value))
        pos = start + size
    return out


def assemble(text):
    """The default document with every placeholder filled -> html."""
    targets = {"default": []}
    current = "default"
    for ftype, name, value in frames(text):
        if name == "write_to_target_document":
            current = value
            targets.setdefault(current, [])
        elif name == "placeholder":
            targets.setdefault(current, []).append(_SLOT.format(value))
        elif name == "" and ftype == "0":
            targets.setdefault(current, []).append(value)
    filled = {}

    def fill(key, depth=0):
        if key in filled:
            return filled[key]
        body = "".join(targets.get(key, []))
        if depth < 20:
            body = _SLOT_RE.sub(lambda m: fill(m.group(1), depth + 1), body)
        filled[key] = body
        return body

    return fill("default")


def jsdata(text):
    """{KEY: parsed json} of every md frame."""
    out = {}
    for _, name, value in frames(text):
        if name != "md":
            continue
        try:
            groups = json.loads(value)
        except ValueError:
            continue
        for group in groups if isinstance(groups, list) else []:
            for pair in group if isinstance(group, list) else []:
                if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and isinstance(pair[1], str):
                    try:
                        out[pair[0]] = json.loads(pair[1])
                    except ValueError:
                        out[pair[0]] = pair[1]
    return out

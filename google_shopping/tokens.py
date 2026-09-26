"""Google Shopping's `shoprs` refinement token: build and decode.

Every filter chip on a udm=28 results page links to
/search?q=<rewritten query>&udm=28&shoprs=<token>, and the legacy
`tbs=mr:1,price:1,ppr_min:…` / `p_ord:` params are IGNORED on udm=28
(verified 2026-09-24/26), so price / sort / rating / sale filters have to
be sent as this token. It is an unsigned base64url protobuf:

  1   = 1
  2   = payload of each active structured filter (repeated, in chip order)
          price        {2: {1: min f64 MICROS, 2: max f64 MICROS, 3: 1}}
          on sale      {9: {3: 1}}
          free ship    {10: {2: 1}}
          rating       {12: {1: stars * 100}}
  3   = kind of the most recent chip (see KIND_*)
  5   = the ORIGINAL query
  6   = one display chip per active filter (repeated):
          {1: kind, 2: label, 3: 2, 4: payload copy, 5: {…}, 7: {attribute ids},
           8: stars, 9: 1 (mode chip on the Deals tab), 10: sort value}
  11  = category id (Google product taxonomy: 529370 Electronics, …)
  12  = 2 (search) | 6 (department browse) | 3 (deal collections)
  17  = sort value (1 price asc, 2 price desc, 3 rating, 4 relevance)

Chips without a payload (condition, store, attribute) act through the
rewritten query Google puts next to the token ("new nike air max",
"best buy wireless earbuds"); build_query() reproduces those rewrites.
"""
import base64
import struct

KIND_ATTRIBUTE = 1
KIND_STORE = 2
KIND_NEARBY = 3
KIND_PRICE = 5
KIND_ON_SALE = 6
KIND_CATEGORY = 9
KIND_CONDITION = 10
KIND_DELIVERY = 20
KIND_SMALL_BUSINESS = 22
KIND_SORT = 23
KIND_FREE_SHIPPING = 25
KIND_RATING = 30

KIND_NAMES = {
    KIND_ATTRIBUTE: "attribute", KIND_STORE: "store", KIND_NEARBY: "nearby", KIND_PRICE: "price",
    KIND_ON_SALE: "on_sale", KIND_CATEGORY: "category", KIND_CONDITION: "condition", KIND_DELIVERY: "delivery",
    KIND_SMALL_BUSINESS: "small_business", KIND_SORT: "sort", KIND_FREE_SHIPPING: "free_shipping",
    KIND_RATING: "rating",
}

SORTS = {"relevance": None, "price_low_to_high": 1, "price_high_to_low": 2, "rating": 3}
SORT_LABELS = {1: "Price low to high", 2: "Price high to low", 3: "Rating high to low", 4: "Relevance"}


# ---- protobuf primitives --------------------------------------------------------------------

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(num, wire):
    return _varint((num << 3) | wire)


def _uint(num, value):
    return _key(num, 0) + _varint(int(value))


def _bytes(num, payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return _key(num, 2) + _varint(len(payload)) + payload


def _double(num, value):
    return _key(num, 1) + struct.pack("<d", float(value))


def b64encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64decode(token):
    token = (token or "").strip().replace("+", "-").replace("/", "_")
    return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))


def _read_varint(buf, i):
    shift = result = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return result, i


def fields(buf):
    """Raw protobuf -> [(field, wire, value)] (length-delimited values stay bytes)."""
    out, i = [], 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _read_varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        elif wire == 2:
            size, i = _read_varint(buf, i)
            value, i = buf[i:i + size], i + size
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.append((num, wire, value))
    return out


# ---- chips ---------------------------------------------------------------------------------

def _money(amount):
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def price_chip(min_price=None, max_price=None):
    rng = b""
    if min_price is not None:
        rng += _double(1, float(min_price) * 1e6)
    if max_price is not None:
        rng += _double(2, float(max_price) * 1e6)
    rng += _uint(3, 1)
    payload = _bytes(2, rng)
    if min_price is not None and max_price is not None:
        label = f"{_money(min_price)} - {_money(max_price)}"
    elif min_price is not None:
        label = f"Over {_money(min_price)}"
    else:
        label = f"Under {_money(max_price)}"
    return {"kind": KIND_PRICE, "label": label, "payload": payload,
            "chip": _uint(3, 2) + _bytes(4, payload) + _bytes(5, _uint(3, 1))}


def on_sale_chip(mode=False):
    payload = _bytes(9, _uint(3, 1))
    return {"kind": KIND_ON_SALE, "label": "On sale", "payload": payload,
            "chip": _uint(3, 2) + _bytes(4, payload) + (_uint(9, 1) if mode else b"")}


def free_shipping_chip():
    payload = _bytes(10, _uint(2, 1))
    return {"kind": KIND_FREE_SHIPPING, "label": "Free shipping", "payload": payload, "chip": _bytes(4, payload)}


def rating_chip(stars):
    payload = _bytes(12, _uint(1, int(stars) * 100))
    return {"kind": KIND_RATING, "label": f"{int(stars)} and up", "payload": payload,
            "chip": _bytes(4, payload) + _uint(8, int(stars))}


def condition_chip(condition):
    return {"kind": KIND_CONDITION, "label": condition.capitalize(), "payload": None, "chip": b""}


def store_chip(store):
    return {"kind": KIND_STORE, "label": store, "payload": None, "chip": b""}


def sort_chip(value):
    return {"kind": KIND_SORT, "label": SORT_LABELS[value], "payload": None,
            "chip": _bytes(5, _uint(2, 1) + _uint(3, 1)) + _uint(10, value), "sort": value}


def _chip_message(chip):
    return _uint(1, chip["kind"]) + _bytes(2, chip["label"]) + chip["chip"]


def build(query, chips=(), category=None, browse=False):
    """The shoprs token for `query` with `chips` applied (in order)."""
    chips = list(chips)
    body = _uint(1, 1)
    for chip in chips:
        if chip.get("payload"):
            body += _bytes(2, chip["payload"])
    if chips:
        body += _uint(3, chips[-1]["kind"])
    body += _bytes(5, query or "")
    for chip in chips:
        body += _bytes(6, _chip_message(chip))
    if category:
        body += _uint(11, int(category))
    body += _uint(12, 6 if browse and not chips else 2)
    sort = next((c["sort"] for c in chips if c.get("sort")), None)
    if sort:
        body += _uint(17, sort)
    return b64encode(body)


def merge(token, chips=(), category=None):
    """A token taken from a filter link (response `filters[].options[].link`)
    with more `chips` applied on top; its payloads, chips, category and
    sort are kept (a new sort chip replaces the old sort)."""
    raw = fields(b64decode(token))
    payloads, chip_msgs, kinds = [], [], []
    query, old_category, old_sort, mode = "", None, None, 2
    new_sort = any(c.get("sort") for c in chips)
    for num, wire, value in raw:
        if num == 2 and wire == 2:
            payloads.append(value)
        elif num == 6 and wire == 2:
            kind = next((v for n, w, v in fields(value) if n == 1 and w == 0), None)
            if new_sort and kind == KIND_SORT:
                continue
            chip_msgs.append(value)
            kinds.append(kind)
        elif num == 5 and wire == 2:
            query = value.decode("utf-8", "replace")
        elif num == 11 and wire == 0:
            old_category = value
        elif num == 17 and wire == 0:
            old_sort = value
        elif num == 12 and wire == 0:
            mode = value
    for chip in chips:
        if chip.get("payload"):
            payloads.append(chip["payload"])
        chip_msgs.append(_chip_message(chip))
        kinds.append(chip["kind"])
    body = _uint(1, 1) + b"".join(_bytes(2, p) for p in payloads)
    if kinds and kinds[-1] is not None:
        body += _uint(3, kinds[-1])
    body += _bytes(5, query) + b"".join(_bytes(6, c) for c in chip_msgs)
    cat = category or old_category
    if cat:
        body += _uint(11, int(cat))
    body += _uint(12, 2 if chip_msgs else mode)
    sort = next((c["sort"] for c in chips if c.get("sort")), None) or (None if new_sort else old_sort)
    if sort:
        body += _uint(17, sort)
    return b64encode(body), query


def browse_token(category):
    """A department page (/search?q=<Department>&shoprs=…&source=depnav):
    just the category id and browse mode, byte-identical to the home page links."""
    return b64encode(_uint(11, int(category)) + _uint(12, 6))


def deals_token(query="", category=None):
    """The Deals tab (shopmd=3): the On-sale chip in mode form."""
    chip = on_sale_chip(mode=True)
    body = (_uint(1, 1) + _bytes(2, chip["payload"]) + _uint(3, KIND_ON_SALE) + _bytes(5, query or "")
            + _bytes(6, _chip_message(chip)))
    if category:
        body += _uint(11, int(category)) + _uint(12, 2)
    return b64encode(body)


def build_query(query, condition=None, store=None, on_sale=False, free_shipping=False, min_price=None,
                max_price=None):
    """Google's own rewrite of `q` next to a token ("new nike air max sale")."""
    q = query
    if store:
        q = f"{store.lower()} {q}"
    if condition in ("new", "used"):
        q = f"{condition} {q}"
    if min_price is not None and max_price is not None:
        q = f"{q} between {_money(min_price)} and {_money(max_price)}"
    elif max_price is not None:
        q = f"{q} under {_money(max_price)}"
    elif min_price is not None:
        q = f"{q} over {_money(min_price)}"
    if on_sale:
        q = f"{q} sale"
    if free_shipping:
        q = f"{q} free shipping"
    return q


# ---- decoding ------------------------------------------------------------------------------------

def decode(token):
    """shoprs token -> {"query", "kind", "category_id", "sort", "chips": [{kind, type, label}]}.
    Raises ValueError on a token that is not a refinement token."""
    try:
        top = fields(b64decode(token))
    except Exception as e:
        raise ValueError(f"not a Google Shopping filter token ({e})")
    out = {"query": None, "kind": None, "category_id": None, "sort": None, "chips": []}
    for num, wire, value in top:
        if num == 5 and wire == 2:
            out["query"] = value.decode("utf-8", "replace")
        elif num == 3 and wire == 0:
            out["kind"] = value
        elif num == 11 and wire == 0:
            out["category_id"] = value
        elif num == 17 and wire == 0:
            out["sort"] = value
        elif num == 6 and wire == 2:
            chip = {"kind": None, "label": None}
            for cn, cw, cv in fields(value):
                if cn == 1 and cw == 0:
                    chip["kind"] = cv
                elif cn == 2 and cw == 2:
                    chip["label"] = cv.decode("utf-8", "replace")
            chip["type"] = KIND_NAMES.get(chip["kind"], "other")
            out["chips"].append(chip)
    if not top or top[0][:2] != (1, 0):
        raise ValueError("not a Google Shopping filter token")
    return out


def token_kind(token):
    """The chip kind a filter link's token applies (None when undecodable)."""
    try:
        return decode(token)["kind"]
    except ValueError:
        return None

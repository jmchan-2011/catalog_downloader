"""
Roblox Model (.rbxm) Parser
Extracts MeshId, TextureId, and Name from SpecialMesh / MeshPart instances.
Supports:
  - Binary .rbxm  (magic: <roblox!)  — post-2015 assets
  - XML .rbxm     (magic: <roblox )  — pre-2015 classic assets
"""
import struct
import re
import lz4.block
from xml.etree import ElementTree as ET

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────
RBXM_MAGIC_BINARY = b"<roblox!"
RBXM_MAGIC_XML    = b"<roblox "
MESH_CLASSES      = {"SpecialMesh", "MeshPart", "FileMesh"}

# ─────────────────────────────────────────────────────────────
# ASSET ID EXTRACTOR
# ─────────────────────────────────────────────────────────────
def _extract_asset_id(value: str) -> int | None:
    if not value:
        return None
    # Handle URLs like https://assetdelivery.roblox.com/v1/?id=12724327580
    m = re.search(r"[?&]id=(\d+)", value)
    if m:
        return int(m.group(1))
    # Handle rbxassetid://123 or plain numbers (5+ digits to avoid version numbers)
    m = re.search(r"(\d{5,})", value)
    return int(m.group(1)) if m else None

# ─────────────────────────────────────────────────────────────
# XML PARSER  (old format — pre-2015)
# ─────────────────────────────────────────────────────────────
def _parse_xml_rbxm(data: bytes) -> list[dict]:
    results = []
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError as e:
        print(f"    [rbxm xml] Parse error: {e}")
        return results

    for item in root.iter("Item"):
        class_name = item.get("class", "")
        if class_name not in MESH_CLASSES:
            continue
        props = item.find("Properties")
        if props is None:
            continue

        inst_name  = class_name
        mesh_id    = None
        texture_id = None

        for prop in props:
            prop_name = prop.get("name", "")
            if prop.tag == "string" and prop_name == "Name":
                inst_name = prop.text or class_name
            elif prop_name in ("MeshId", "TextureId"):
                raw_val = ""
                url_el = prop.find("url")
                if url_el is not None and url_el.text:
                    raw_val = url_el.text
                elif prop.text:
                    raw_val = prop.text
                aid = _extract_asset_id(raw_val)
                if prop_name == "MeshId":
                    mesh_id = aid
                else:
                    texture_id = aid

        if mesh_id or texture_id:
            results.append({
                "name":       inst_name,
                "class":      class_name,
                "mesh_id":    mesh_id,
                "texture_id": texture_id,
            })
    return results

# ─────────────────────────────────────────────────────────────
# LOW-LEVEL BINARY CHUNK READER
# ─────────────────────────────────────────────────────────────
def _decompress_chunk(payload: bytes, decompressed_size: int) -> bytes:
    if decompressed_size == 0:
        return payload
    return lz4.block.decompress(payload, uncompressed_size=decompressed_size)

def _read_chunks(data: bytes) -> list[tuple[str, bytes]]:
    if not data.startswith(RBXM_MAGIC_BINARY):
        raise ValueError("Not a valid binary .rbxm file (bad magic bytes)")
    offset = 32
    chunks = []
    while offset < len(data):
        if offset + 16 > len(data):
            break
        name_raw    = data[offset:offset + 4]
        comp_size   = struct.unpack_from("<I", data, offset + 4)[0]
        decomp_size = struct.unpack_from("<I", data, offset + 8)[0]
        offset     += 16
        payload_size = comp_size if comp_size > 0 else decomp_size
        payload      = data[offset:offset + payload_size]
        offset      += payload_size
        try:
            name = name_raw.rstrip(b"\x00").decode("ascii")
        except Exception:
            name = "????"
        try:
            body = _decompress_chunk(payload, decomp_size)
        except Exception:
            body = payload
        chunks.append((name, body))
        if name == "END":
            break
    return chunks

# ─────────────────────────────────────────────────────────────
# INTERLEAVED INT32 DECODER
# ─────────────────────────────────────────────────────────────
def _decode_interleaved_int32(raw: bytes, count: int) -> list[int]:
    if len(raw) < count * 4:
        return []
    result = []
    for i in range(count):
        b3  = raw[i]
        b2  = raw[i + count]
        b1  = raw[i + count * 2]
        b0  = raw[i + count * 3]
        val = (b3 << 24) | (b2 << 16) | (b1 << 8) | b0
        val = (val >> 1) ^ -(val & 1)
        result.append(val)
    return result

# ─────────────────────────────────────────────────────────────
# INST CHUNK
# ─────────────────────────────────────────────────────────────
def _parse_inst_chunk(body: bytes) -> tuple[int, str, list[int]]:
    offset = 0
    class_index = struct.unpack_from("<I", body, offset)[0]; offset += 4
    name_len    = struct.unpack_from("<I", body, offset)[0]; offset += 4
    class_name  = body[offset:offset + name_len].decode("utf-8", errors="replace")
    offset     += name_len
    offset     += 1
    count       = struct.unpack_from("<I", body, offset)[0]; offset += 4
    raw_refs    = body[offset:offset + count * 4]
    referents   = _decode_interleaved_int32(raw_refs, count)
    for i in range(1, len(referents)):
        referents[i] += referents[i - 1]
    return class_index, class_name, referents

# ─────────────────────────────────────────────────────────────
# PROP CHUNK
# ─────────────────────────────────────────────────────────────
PROP_TYPE_STRING  = 0x01
PROP_TYPE_CONTENT = 0x03

def _parse_prop_chunk_strings(body: bytes) -> tuple[int, str, list[str]]:
    offset = 0
    class_index = struct.unpack_from("<I", body, offset)[0]; offset += 4
    name_len    = struct.unpack_from("<I", body, offset)[0]; offset += 4
    prop_name   = body[offset:offset + name_len].decode("utf-8", errors="replace")
    offset     += name_len
    prop_type   = body[offset]; offset += 1
    values = []
    if prop_type in (PROP_TYPE_STRING, PROP_TYPE_CONTENT):
        while offset < len(body):
            if offset + 4 > len(body):
                break
            slen = struct.unpack_from("<I", body, offset)[0]; offset += 4
            if offset + slen > len(body):
                break
            val = body[offset:offset + slen].decode("utf-8", errors="replace")
            offset += slen
            values.append(val)
    return class_index, prop_name, values

# ─────────────────────────────────────────────────────────────
# BINARY PARSER
# ─────────────────────────────────────────────────────────────
def _parse_binary_rbxm(data: bytes) -> list[dict]:
    try:
        chunks = _read_chunks(data)
    except Exception as e:
        print(f"    [rbxm binary] Failed to read chunks: {e}")
        return []

    class_map: dict[int, tuple[str, list[int]]] = {}
    for name, body in chunks:
        if name == "INST":
            try:
                ci, cname, refs = _parse_inst_chunk(body)
                class_map[ci] = (cname, refs)
            except Exception:
                pass

    prop_data: dict[int, dict[str, list[str]]] = {}
    for name, body in chunks:
        if name == "PROP":
            try:
                ci, pname, vals = _parse_prop_chunk_strings(body)
                if vals:
                    if ci not in prop_data:
                        prop_data[ci] = {}
                    prop_data[ci][pname] = vals
            except Exception:
                pass

    results = []
    for ci, (cname, refs) in class_map.items():
        if cname not in MESH_CLASSES:
            continue
        props       = prop_data.get(ci, {})
        names       = props.get("Name",      [])
        mesh_ids    = props.get("MeshId",    [])
        texture_ids = props.get("TextureId", [])

        count = max(len(refs), len(mesh_ids), 1)
        for i in range(count):
            inst_name = names[i]       if i < len(names)       else cname
            raw_mesh  = mesh_ids[i]    if i < len(mesh_ids)    else ""
            raw_tex   = texture_ids[i] if i < len(texture_ids) else ""
            mesh_aid  = _extract_asset_id(raw_mesh)
            tex_aid   = _extract_asset_id(raw_tex)
            if mesh_aid or tex_aid:
                results.append({
                    "name":       inst_name,
                    "class":      cname,
                    "mesh_id":    mesh_aid,
                    "texture_id": tex_aid,
                })
    return results

# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────
def extract_mesh_assets(rbxm_bytes: bytes) -> list[dict]:
    if rbxm_bytes[:7] == b"<roblox":
        if rbxm_bytes[:8] == RBXM_MAGIC_BINARY:
            return _parse_binary_rbxm(rbxm_bytes)
        else:
            return _parse_xml_rbxm(rbxm_bytes)
    print(f"    [rbxm] Unknown format (magic: {rbxm_bytes[:8]!r})")
    return []
"""
Roblox Avatar Asset Downloader & OBJ Converter
────────────────────────────────────────────────
Requirements:  pip install requests lz4
Usage:         python roblox_asset_downloader.py
"""

import os
import re
import time
import json
import struct
import requests
from rbxm_parser import extract_mesh_assets


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

OUTPUT_DIR             = "roblox_assets"
PROGRESS_FILE          = "progress.json"
CATALOG_CACHE_FILE     = "catalog_cache.json"
OFFSALE_CACHE_FILE     = "offsale_cache.json"
SLEEP_BETWEEN_REQUESTS = 1.2
SLEEP_BETWEEN_PASSES   = 15
SLEEP_ON_RATE_LIMIT    = 60
MAX_ASSETS             = None

ROBLOX_USER_ID = 1

# asset type ID → (display name, folder name)
ASSET_TYPE_INFO = {
    8:  ("Hat",      "hat"),
    41: ("Hair",     "hair"),
    42: ("Face",     "face"),
    43: ("Neck",     "neck"),
    44: ("Shoulder", "shoulder"),
    45: ("Front",    "front"),
    46: ("Back",     "back"),
    47: ("Waist",    "waist"),
}
WANTED_ASSET_TYPES = set(ASSET_TYPE_INFO.keys())

R6_PART_TYPES = {
    1:  "Head",
    15: "Torso",
    16: "LeftArm",
    17: "RightArm",
    18: "LeftLeg",
    19: "RightLeg",
}

R15_PART_TYPES = {
    27: "Head",
    28: "LeftArm",
    29: "LeftFoot",
    30: "LeftHand",
    31: "LeftLeg",
    32: "LowerTorso",
    33: "RightArm",
    34: "RightFoot",
    35: "RightHand",
    36: "RightLeg",
    37: "UpperTorso",
}

ALL_BODY_PART_TYPES = {**R6_PART_TYPES, **R15_PART_TYPES}

SORT_ORDERS = [0, 4, 5]

PRICE_SLICES = [
    ("Free",         0,    0),
    ("1–10",         1,   10),
    ("11–50",       11,   50),
    ("51–75",       51,   75),
    ("76–100",      76,  100),
    ("101–125",    101,  125),
    ("126–150",    126,  150),
    ("151–175",    151,  175),
    ("176–200",    176,  200),
    ("201–250",    201,  250),
    ("251–300",    251,  300),
    ("301–350",    301,  350),
    ("351–400",    351,  400),
    ("401–500",    401,  500),
    ("501–750",    501,  750),
    ("751–1000",   751, 1000),
    ("1001–2000", 1001, 2000),
    ("2001–5000", 2001, 5000),
    ("5001+",     5001, None),
]

# ── Rolimons asset type IDs ────────────────────────────────────
# These map to Roblox asset type IDs for filtering
ROLIMONS_ASSET_TYPES = {
    8:  "Hat",
    41: "Hair Accessory",
    42: "Face Accessory",
    43: "Neck Accessory",
    44: "Shoulder Accessory",
    45: "Front Accessory",
    46: "Back Accessory",
    47: "Waist Accessory",
}


# ═══════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

CATALOG_SEARCH_URL  = "https://catalog.roproxy.com/v1/search/items/details"
HAIR_SEARCH_URL     = "https://catalog.roproxy.com/v1/search/items"
ASSET_DETAILS_URL   = "https://catalog.roproxy.com/v1/catalog/items/details"
ASSET_DELIVERY_URL  = "https://assetdelivery.roproxy.com/v2/assetId/{asset_id}"
BUNDLE_DETAILS_URL  = "https://catalog.roproxy.com/v1/bundles/{bundle_id}/details"
USER_BUNDLES_URL    = "https://catalog.roproxy.com/v1/users/{user_id}/bundles"
CATALOG_ITEM_URL    = "https://catalog.roproxy.com/v1/catalog/items/details"

# Rolimons items API — returns all tracked items with asset type + offsale status
ROLIMONS_ITEMS_URL  = "https://www.rolimons.com/itemapi/itemdetails"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.roblox.com/",
    "Origin":          "https://www.roblox.com",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ═══════════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════════

def load_progress() -> set[int]:
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE, "r") as f:
            data = json.load(f)
        completed = set(data.get("completed", []))
        print(f"[Resume] {len(completed)} assets already done — skipping those.")
        return completed
    except Exception as e:
        print(f"[Resume] Could not read progress file: {e} — starting fresh")
        return set()


def mark_done(asset_id: int, completed: set[int]):
    completed.add(asset_id)
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"completed": sorted(completed)}, f)


# ═══════════════════════════════════════════════════════════════
# CATALOG CACHE
# ═══════════════════════════════════════════════════════════════

def load_catalog_cache() -> list[dict] | None:
    if not os.path.exists(CATALOG_CACHE_FILE):
        return None
    try:
        with open(CATALOG_CACHE_FILE, "r") as f:
            data = json.load(f)
        items = data.get("items", [])
        ts    = data.get("timestamp", "unknown")
        print(f"[Cache] Loaded {len(items)} catalog items (fetched: {ts})")
        print(f"[Cache] Delete {CATALOG_CACHE_FILE} to force a fresh fetch.\n")
        return items
    except Exception as e:
        print(f"[Cache] Could not read cache: {e} — re-fetching")
        return None


def save_catalog_cache(items: list[dict]):
    from datetime import datetime
    with open(CATALOG_CACHE_FILE, "w") as f:
        json.dump({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count":     len(items),
            "items":     items,
        }, f)
    print(f"[Cache] Saved {len(items)} items to {CATALOG_CACHE_FILE}\n")


# ═══════════════════════════════════════════════════════════════
# OFFSALE CACHE
# ═══════════════════════════════════════════════════════════════

def load_offsale_cache() -> list[dict] | None:
    if not os.path.exists(OFFSALE_CACHE_FILE):
        return None
    try:
        with open(OFFSALE_CACHE_FILE, "r") as f:
            data = json.load(f)
        items = data.get("items", [])
        ts    = data.get("timestamp", "unknown")
        print(f"[Offsale Cache] Loaded {len(items)} offsale items (fetched: {ts})")
        print(f"[Offsale Cache] Delete {OFFSALE_CACHE_FILE} to force a fresh fetch.\n")
        return items
    except Exception as e:
        print(f"[Offsale Cache] Could not read: {e} — re-fetching")
        return None


def save_offsale_cache(items: list[dict]):
    from datetime import datetime
    with open(OFFSALE_CACHE_FILE, "w") as f:
        json.dump({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count":     len(items),
            "items":     items,
        }, f)
    print(f"[Offsale Cache] Saved {len(items)} items to {OFFSALE_CACHE_FILE}\n")


# ════════════════════════��══════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════

def to_snake_case(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\s\-]", "", name)
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    name = re.sub(r"_+", "_", name)
    return name.lower().strip("_")


def safe_get(url: str, **kwargs) -> requests.Response | None:
    wait = SLEEP_ON_RATE_LIMIT
    for attempt in range(6):
        try:
            r = SESSION.get(url, timeout=30, **kwargs)
            if r.status_code == 429:
                print(f"  [Rate limited] Waiting {wait}s … (attempt {attempt+1})")
                time.sleep(wait)
                wait = min(wait * 2, 300)
                continue
            if r.status_code == 503:
                print(f"  [503] Waiting 30s …")
                time.sleep(30)
                continue
            return r
        except requests.RequestException as e:
            print(f"  [Request error] {e} (attempt {attempt+1})")
            time.sleep(5)
    print(f"  [FAILED] Gave up: {url}")
    return None


def safe_post(url: str, **kwargs) -> requests.Response | None:
    wait = SLEEP_ON_RATE_LIMIT
    for attempt in range(6):
        try:
            r = SESSION.post(url, timeout=30, **kwargs)
            if r.status_code == 429:
                print(f"  [Rate limited] Waiting {wait}s … (attempt {attempt+1})")
                time.sleep(wait)
                wait = min(wait * 2, 300)
                continue
            return r
        except requests.RequestException as e:
            print(f"  [Request error] {e} (attempt {attempt+1})")
            time.sleep(5)
    return None


# ═══════════════════════════════════════════════════════════════
# MESH PARSING
# ═══════════════════════════════════════════════════════════════

def _parse_mesh_v1(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    vertices, normals, uvs, faces = [], [], [], []
    try:
        face_count = int(lines[2])
        for face_line in lines[3:3+face_count]:
            nums = re.findall(r"[-\d.e+]+", face_line)
            if len(nums) < 27: continue
            fi_v, fi_n, fi_u = [], [], []
            for v in range(3):
                b = v * 9
                vx,vy,vz = float(nums[b]),   float(nums[b+1]), float(nums[b+2])
                nx,ny,nz = float(nums[b+3]), float(nums[b+4]), float(nums[b+5])
                u,tv     = float(nums[b+6]), float(nums[b+7])
                vertices.append((vx,vy,vz)); fi_v.append(len(vertices))
                normals.append((nx,ny,nz));  fi_n.append(len(normals))
                uvs.append((u,tv));          fi_u.append(len(uvs))
            faces.append(list(zip(fi_v, fi_u, fi_n)))
    except (IndexError, ValueError) as e:
        print(f"    [mesh v1 error] {e}")
    return vertices, normals, uvs, faces


def _parse_mesh_binary(data: bytes):
    vertices, normals, uvs, faces = [], [], [], []
    try:
        nl      = data.index(b"\n")
        header  = data[:nl].decode("utf-8", errors="ignore").strip()
        vm      = re.search(r"version\s+([\d.]+)", header, re.I)
        if not vm: return vertices, normals, uvs, faces
        version = vm.group(1)
        pos     = nl + 1

        if version.startswith("2"):
            sz_header = struct.unpack_from("<H", data, pos)[0]
            sz_vertex = struct.unpack_from("<H", data, pos+2)[0]
            sz_face   = struct.unpack_from("<H", data, pos+4)[0]
            num_verts = struct.unpack_from("<I", data, pos+6)[0]
            num_faces = struct.unpack_from("<I", data, pos+10)[0]
            vpos = pos + sz_header
            for i in range(num_verts):
                base = vpos + i*sz_vertex
                if base+28 > len(data): break
                x,y,z    = struct.unpack_from("<fff", data, base)
                nx,ny,nz = struct.unpack_from("<fff", data, base+12)
                u,v      = struct.unpack_from("<ff",  data, base+24)
                vertices.append((x,y,z)); normals.append((nx,ny,nz)); uvs.append((u,v))
            fpos = vpos + num_verts*sz_vertex
            for i in range(num_faces):
                base = fpos + i*sz_face
                if base+12 > len(data): break
                a,b,c = struct.unpack_from("<III", data, base)
                faces.append([(a+1,a+1,a+1),(b+1,b+1,b+1),(c+1,c+1,c+1)])

        elif version.startswith(("3","4","5")):
            sz_header = struct.unpack_from("<H", data, pos)[0]
            sz_vertex = struct.unpack_from("<H", data, pos+2)[0]
            num_verts = struct.unpack_from("<I", data, pos+12)[0]
            num_faces = struct.unpack_from("<I", data, pos+16)[0]
            sz_face   = 12
            vpos = pos + sz_header
            for i in range(num_verts):
                base = vpos + i*sz_vertex
                if base+28 > len(data): break
                x,y,z    = struct.unpack_from("<fff", data, base)
                nx,ny,nz = struct.unpack_from("<fff", data, base+12)
                u,v      = struct.unpack_from("<ff",  data, base+24)
                vertices.append((x,y,z)); normals.append((nx,ny,nz)); uvs.append((u,v))
            fpos = vpos + num_verts*sz_vertex
            for i in range(num_faces):
                base = fpos + i*sz_face
                if base+12 > len(data): break
                a,b,c = struct.unpack_from("<III", data, base)
                faces.append([(a+1,a+1,a+1),(b+1,b+1,b+1),(c+1,c+1,c+1)])
    except Exception as e:
        print(f"    [mesh binary error] {e}")
    return vertices, normals, uvs, faces


def parse_roblox_mesh(raw: bytes):
    try:
        header = raw[:20].decode("utf-8", errors="ignore").lower()
    except Exception:
        header = ""
    if "version 1" in header:
        return _parse_mesh_v1(raw.decode("utf-8", errors="ignore"))
    return _parse_mesh_binary(raw)


# ═══════════════════════════════════════════════════════════════
# OBJ / MTL WRITERS
# ═══════════════════════════════════════════════════════════════

def write_obj(vertices, normals, uvs, faces, obj_path, mtl_name, tex_name):
    with open(obj_path, "w") as f:
        f.write("# Exported by roblox_asset_downloader.py\n")
        f.write(f"mtllib {mtl_name}\n\n")
        for x,y,z in vertices:
            f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        f.write("\n")
        for u,v in uvs:
            f.write(f"vt {u:.6f} {1.0-v:.6f}\n")
        f.write("\n")
        for nx,ny,nz in normals:
            f.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")
        f.write("\nusemtl material0\n")
        for face in faces:
            f.write("f " + " ".join(f"{vi}/{uvi}/{ni}" for vi,uvi,ni in face) + "\n")


def write_mtl(mtl_path, texture_filename):
    with open(mtl_path, "w") as f:
        f.write("# Exported by roblox_asset_downloader.py\n")
        f.write("newmtl material0\n")
        f.write("Ka 1.000 1.000 1.000\n")
        f.write("Kd 1.000 1.000 1.000\n")
        f.write("Ks 0.000 0.000 0.000\n")
        f.write("d 1.0\n")
        f.write("illum 2\n")
        if texture_filename:
            f.write(f"map_Kd {texture_filename}\n")


# ═══════════════════════════════════════════════════════════════
# TEXTURE + MESH HELPERS
# ═══════════════════════════════════════════════════════════════

def get_asset_download_url(asset_id: int) -> str | None:
    r = safe_get(ASSET_DELIVERY_URL.format(asset_id=asset_id))
    if r is None or not r.ok:
        return None
    locs = r.json().get("locations", [])
    return locs[0].get("location") if locs else None


def download_texture(texture_id: int, tex_path: str) -> bool:
    url = get_asset_download_url(texture_id)
    if not url: return False
    r = safe_get(url)
    if r is None or not r.ok: return False
    ct = r.headers.get("content-type", "")
    if "image" in ct or r.content[:4] in (b"\x89PNG", b"\xff\xd8\xff"):
        with open(tex_path, "wb") as f:
            f.write(r.content)
        return True
    return False


def save_mesh_to_folder(mesh_bytes: bytes, texture_id: int | None,
                        folder: str, base_name: str) -> bool:
    vertices, normals, uvs, faces = parse_roblox_mesh(mesh_bytes)
    if not vertices or not faces:
        return False
    os.makedirs(folder, exist_ok=True)
    obj_path = os.path.join(folder, f"{base_name}.obj")
    mtl_path = os.path.join(folder, f"{base_name}.mtl")
    tex_path = os.path.join(folder, f"{base_name}.png")
    tex_ok   = download_texture(texture_id, tex_path) if texture_id else False
    write_mtl(mtl_path, f"{base_name}.png" if tex_ok else "")
    write_obj(vertices, normals, uvs, faces, obj_path,
              f"{base_name}.mtl", f"{base_name}.png")
    print(f"      [{'✓ tex' if tex_ok else '✗ no tex'}] "
          f"{base_name}.obj  ({len(vertices)}v {len(faces)}f)")
    return True


def download_and_save(asset_id: int, folder: str,
                      base_name: str,
                      texture_id: int | None = None) -> bool:
    asset_url = get_asset_download_url(asset_id)
    if not asset_url:
        print(f"      [SKIP] No CDN URL for {asset_id}")
        return False
    r = safe_get(asset_url)
    if r is None or not r.ok:
        print(f"      [SKIP] Download failed for {asset_id}")
        return False

    raw     = r.content
    is_rbxm = raw[:7] == b"<roblox"
    is_mesh = b"version" in raw[:20].lower()

    if is_rbxm:
        mesh_assets = extract_mesh_assets(raw)
        if not mesh_assets:
            print(f"      [SKIP] No meshes in .rbxm {asset_id}")
            return False
        saved = False
        for entry in mesh_assets:
            mesh_id   = entry.get("mesh_id")
            tex_id    = entry.get("texture_id") or texture_id
            inst_name = to_snake_case(entry.get("name", "mesh")) or "mesh"
            part_name = inst_name if len(mesh_assets) > 1 else base_name
            if not mesh_id: continue
            mesh_url = get_asset_download_url(mesh_id)
            if not mesh_url: continue
            r_mesh = safe_get(mesh_url)
            if r_mesh is None or not r_mesh.ok: continue
            if save_mesh_to_folder(r_mesh.content, tex_id, folder, part_name):
                saved = True
            time.sleep(SLEEP_BETWEEN_REQUESTS)
        return saved
    elif is_mesh:
        return save_mesh_to_folder(raw, texture_id, folder, base_name)
    else:
        print(f"      [SKIP] Unknown format {asset_id} ({raw[:8]!r})")
        return False


# ═════════════════��═════════════════════════════════════════════
# OFFSALE ITEMS — Rolimons fetch
# ═══════════════════════════════════════════════════════════════

def fetch_offsale_items(known_onsale_ids: set[int]) -> list[dict]:
    """
    Fetch offsale accessory items from Rolimons item database.
    Rolimons tracks ALL Roblox limited/offsale items with their
    asset IDs, names and asset types.

    Returns list of {id, name, assetType} dicts that are:
    - Made by Roblox (creator check via catalog API)
    - Offsale (not in known_onsale_ids)
    - Accessory type we care about
    """
    cached = load_offsale_cache()
    if cached is not None:
        # Filter out any that are now in onsale
        return [i for i in cached if i["id"] not in known_onsale_ids]

    print("[Offsale] Fetching item database from Rolimons …")
    r = safe_get(ROLIMONS_ITEMS_URL)
    if r is None or not r.ok:
        print(f"  [SKIP] Rolimons unavailable ({r.status_code if r else 'no response'})")
        return []

    try:
        data  = r.json()
        items = data.get("items", {})
    except Exception as e:
        print(f"  [SKIP] Could not parse Rolimons response: {e}")
        return []

    # Rolimons item format:
    # { "asset_id": [name, acronym, value, default_value,
    #                demand, trend, projected, hyped,
    #                rare, rat, ...]  }
    # We need to verify each item is by Roblox + get asset type
    # via the catalog details API in batches

    all_ids    = [int(aid) for aid in items.keys()]
    offsale    = [aid for aid in all_ids if aid not in known_onsale_ids]
    print(f"  Rolimons total: {len(all_ids)}  "
          f"| not in our onsale set: {len(offsale)}")

    # ── Verify via catalog API in batches of 120 ──────────────
    print("  Verifying asset types + Roblox creator via catalog API …")
    verified   = []
    chunk_size = 120

    for i in range(0, len(offsale), chunk_size):
        chunk   = offsale[i:i+chunk_size]
        payload = {"items": [{"itemType": "Asset", "id": aid} for aid in chunk]}
        resp    = safe_post(CATALOG_ITEM_URL, json=payload)

        if resp is None or not resp.ok:
            print(f"  [WARN] Catalog batch {i//chunk_size+1} failed — skipping chunk")
            time.sleep(5)
            continue

        for d in resp.json().get("data", []):
            aid        = d.get("id")
            asset_type = d.get("assetType")
            creator    = d.get("creatorTargetId")
            name       = d.get("name", f"asset_{aid}")

            # Only keep Roblox-made accessories we want
            if (asset_type in WANTED_ASSET_TYPES
                    and creator == ROBLOX_USER_ID
                    and aid not in known_onsale_ids):
                verified.append({
                    "id":        aid,
                    "name":      name,
                    "assetType": asset_type,
                })

        prog = min(i + chunk_size, len(offsale))
        print(f"  Verified {prog}/{len(offsale)} "
              f"→ {len(verified)} offsale accessories so far    ",
              end="\r")
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    print()
    print(f"  ── Total offsale accessories found: {len(verified)}\n")

    # Breakdown
    counts = {}
    for item in verified:
        t = item["assetType"]
        counts[t] = counts.get(t, 0) + 1
    for tid in sorted(counts):
        info = ASSET_TYPE_INFO.get(tid, ("?", "?"))
        print(f"    {info[0]}: {counts[tid]}")
    print()

    save_offsale_cache(verified)
    return verified


# ═══════════════════════════════════════════════════════════════
# CATALOG FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_pass(label, min_price, max_price, sort_type):
    items, cursor, page = [], "", 0
    while True:
        page += 1
        params = {
            "Category": 11, "CreatorType": 1,
            "CreatorTargetId": ROBLOX_USER_ID,
            "Limit": 30, "SortType": sort_type,
            "SortAggregation": 5, "MinPrice": min_price,
        }
        if max_price is not None: params["MaxPrice"] = max_price
        if cursor: params["Cursor"] = cursor
        r = safe_get(CATALOG_SEARCH_URL, params=params)
        if r is None or not r.ok:
            if r: print(f"\n  [API {r.status_code}]: {r.text[:150]}")
            break
        data  = r.json()
        batch = data.get("data", [])
        items.extend(batch)
        print(f"  [{label}] page {page}: +{len(batch)} "
              f"(total: {len(items)})    ", end="\r")
        cursor = data.get("nextPageCursor")
        if not cursor: break
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    print()
    return items


def fetch_hair_pass(sort_type):
    items, cursor, page = [], "", 0
    while True:
        page += 1
        params = {
            "AssetType": 41, "CreatorType": 1,
            "CreatorTargetId": ROBLOX_USER_ID,
            "Limit": 30, "SortType": sort_type, "SortAggregation": 5,
        }
        if cursor: params["Cursor"] = cursor
        r = safe_get(HAIR_SEARCH_URL, params=params)
        if r is None or not r.ok:
            if r: print(f"\n  [API {r.status_code}]: {r.text[:150]}")
            break
        data  = r.json()
        batch = data.get("data", [])
        items.extend(batch)
        print(f"  [Hair sort={sort_type}] page {page}: +{len(batch)} "
              f"(total: {len(items)})    ", end="\r")
        cursor = data.get("nextPageCursor")
        if not cursor: break
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    print()
    return items


def fetch_all_accessories() -> list[dict]:
    cached = load_catalog_cache()
    if cached is not None:
        return cached

    print("[Catalog] Fetching accessories via price-range slicing …\n")
    seen, all_raw = set(), []
    sort_names   = {0: "Relevance", 4: "PriceAsc", 5: "PriceDesc"}
    total_passes = len(PRICE_SLICES) * len(SORT_ORDERS)
    pass_num     = 0

    for label, min_price, max_price in PRICE_SLICES:
        for sort_type in SORT_ORDERS:
            pass_num  += 1
            full_label = f"{label}/{sort_names[sort_type]}"
            print(f"  Pass {pass_num}/{total_passes}: '{full_label}' …")
            batch = fetch_pass(full_label, min_price, max_price, sort_type)
            new   = [i for i in batch if i["id"] not in seen]
            if not new and pass_num > 1:
                print(f"  ✓ 0 new — skipping remaining sorts for '{label}'\n")
                break
            seen.update(i["id"] for i in new)
            all_raw.extend(new)
            print(f"  ✓ {len(batch)} fetched, {len(new)} new "
                  f"→ {len(all_raw)} unique\n")
            time.sleep(SLEEP_BETWEEN_PASSES)

    for sort_type in SORT_ORDERS:
        sort_name = sort_names[sort_type]
        print(f"  Hair/{sort_name} …")
        batch = fetch_hair_pass(sort_type)
        new   = [i for i in batch if i["id"] not in seen]
        if not new:
            print(f"  ✓ 0 new — skipping remaining hair sorts\n")
            break
        seen.update(i["id"] for i in new)
        all_raw.extend(new)
        print(f"  ✓ Hair/{sort_name}: {len(new)} new "
              f"→ {len(all_raw)} unique\n")
        time.sleep(SLEEP_BETWEEN_PASSES)

    kept = [i for i in all_raw if i.get("assetType") in WANTED_ASSET_TYPES]
    save_catalog_cache(kept)
    print("  Breakdown:")
    counts = {}
    for item in kept:
        counts[item.get("assetType", 0)] = \
            counts.get(item.get("assetType", 0), 0) + 1
    for tid in sorted(counts):
        info = ASSET_TYPE_INFO.get(tid, ("?", "?"))
        print(f"    {info[0]}: {counts[tid]}")
    print(f"  ── Total: {len(kept)}\n")
    return kept


# ═══════════════════════════════════════════════════════════════
# BUNDLE PROCESSING
# ═══════════════════════════════════════════════════════════════

def fetch_catalog_bundles() -> list[dict]:
    print("[Catalog] Fetching bundles …")
    items, seen, cursor = [], set(), ""
    while True:
        params = {"limit": 100, "sortOrder": "Asc"}
        if cursor: params["cursor"] = cursor
        r = safe_get(USER_BUNDLES_URL.format(user_id=ROBLOX_USER_ID),
                     params=params)
        if r is None or not r.ok: break
        data = r.json()
        for item in data.get("data", []):
            if item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
        print(f"  Bundles so far: {len(items)}")
        cursor = data.get("nextPageCursor")
        if not cursor: break
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    print(f"  Total bundles: {len(items)}\n")
    return items


def process_bundle(bundle_id: int, bundle_name: str,
                   completed: set[int],
                   acc_completed: set[int]) -> None:
    r = safe_get(BUNDLE_DETAILS_URL.format(bundle_id=bundle_id))
    if r is None or not r.ok:
        print(f"  [SKIP] Could not get details for bundle {bundle_id}")
        return
    details = r.json()
    items   = details.get("items", [])

    safe_bname   = to_snake_case(bundle_name) or f"bundle_{bundle_id}"
    bundle_root  = os.path.join(OUTPUT_DIR, "bundles", safe_bname)
    r6_folder    = os.path.join(bundle_root, "r6")
    r15_folder   = os.path.join(bundle_root, "r15")
    main_acc_dir = os.path.join(OUTPUT_DIR, "accessories")

    n_r6  = sum(1 for i in items if i.get("assetType") in R6_PART_TYPES)
    n_r15 = sum(1 for i in items if i.get("assetType") in R15_PART_TYPES)
    n_acc = sum(1 for i in items if i.get("assetType") in WANTED_ASSET_TYPES)
    print(f"  '{bundle_name}'  (R6={n_r6} R15={n_r15} acc={n_acc})")

    for item in items:
        if item.get("type") != "Asset":
            continue

        asset_id   = item.get("id")
        asset_name = item.get("name", f"asset_{asset_id}")
        asset_type = item.get("assetType", 0)
        safe_aname = to_snake_case(asset_name) or f"asset_{asset_id}"

        if not asset_id:
            continue

        if asset_type in R6_PART_TYPES:
            part_name = R6_PART_TYPES[asset_type]
            if asset_id in completed:
                print(f"    [skip R6] {part_name}")
                continue
            print(f"    [R6] {part_name}  ← {asset_name}")
            if download_and_save(asset_id, r6_folder, part_name):
                mark_done(asset_id, completed)
            time.sleep(SLEEP_BETWEEN_REQUESTS)

        elif asset_type in R15_PART_TYPES:
            part_name = R15_PART_TYPES[asset_type]
            if asset_id in completed:
                print(f"    [skip R15] {part_name}")
                continue
            print(f"    [R15] {part_name}  ← {asset_name}")
            if download_and_save(asset_id, r15_folder, part_name):
                mark_done(asset_id, completed)
            time.sleep(SLEEP_BETWEEN_REQUESTS)

        elif asset_type in WANTED_ASSET_TYPES:
            type_name   = ASSET_TYPE_INFO[asset_type][0]
            type_folder = ASSET_TYPE_INFO[asset_type][1]

            bundle_acc_path = os.path.join(
                bundle_root, "accessories", type_folder, safe_aname)
            main_acc_path = os.path.join(
                main_acc_dir, type_folder, safe_aname)

            if asset_id not in completed:
                print(f"    [{type_name}→bundle] {asset_name}")
                download_and_save(asset_id, bundle_acc_path, safe_aname)
                time.sleep(SLEEP_BETWEEN_REQUESTS)

            if asset_id not in acc_completed:
                print(f"    [{type_name}→accessories/] {asset_name}")
                if download_and_save(asset_id, main_acc_path, safe_aname):
                    acc_completed.add(asset_id)
                time.sleep(SLEEP_BETWEEN_REQUESTS)
            else:
                print(f"    [{type_name}] {asset_name} already in accessories/")

            mark_done(asset_id, completed)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    # Create base folders
    for _, folder_name in ASSET_TYPE_INFO.values():
        os.makedirs(os.path.join(OUTPUT_DIR, "accessories", folder_name),
                    exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "bundles"), exist_ok=True)

    completed     = load_progress()
    acc_completed = set(completed)

    # ── Step 1: Onsale accessories ────────────────────────────
    accessory_items = fetch_all_accessories()
    onsale_ids      = set(i["id"] for i in accessory_items)
    print(f"[Accessories] {len(accessory_items)} onsale items found\n")

    items = accessory_items if not MAX_ASSETS else accessory_items[:MAX_ASSETS]
    for i, item in enumerate(items, 1):
        aid        = item.get("id")
        name       = item.get("name", f"asset_{aid}")
        asset_type = item.get("assetType", 0)

        if aid in completed:
            print(f"  [skip {i}/{len(items)}] {name}")
            continue

        type_folder = ASSET_TYPE_INFO.get(asset_type, (None, "other"))[1]
        safe_name   = to_snake_case(name) or f"asset_{aid}"
        folder      = os.path.join(OUTPUT_DIR, "accessories",
                                   type_folder, safe_name)

        print(f"  [{i}/{len(items)}] [{type_folder}] {name}  (id={aid})")
        if download_and_save(aid, folder, safe_name):
            mark_done(aid, completed)
            acc_completed.add(aid)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    # ── Step 2: Offsale accessories ───────────────────────────
    print("\n" + "=" * 60)
    print("[Offsale] Fetching offsale accessories …\n")
    offsale_items = fetch_offsale_items(onsale_ids)
    print(f"[Offsale] {len(offsale_items)} offsale items to download\n")

    for i, item in enumerate(offsale_items, 1):
        aid        = item.get("id")
        name       = item.get("name", f"asset_{aid}")
        asset_type = item.get("assetType", 0)

        if aid in completed:
            print(f"  [skip {i}/{len(offsale_items)}] {name}")
            continue

        type_folder = ASSET_TYPE_INFO.get(asset_type, (None, "other"))[1]
        safe_name   = to_snake_case(name) or f"asset_{aid}"
        folder      = os.path.join(OUTPUT_DIR, "accessories",
                                   type_folder, safe_name)

        print(f"  [{i}/{len(offsale_items)}] [offsale/{type_folder}] "
              f"{name}  (id={aid})")
        if download_and_save(aid, folder, safe_name):
            mark_done(aid, completed)
            acc_completed.add(aid)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    # ── Step 3: Bundles ───────────────────────────────────────
    print("\n" + "=" * 60)
    bundles = fetch_catalog_bundles()
    print(f"[Bundles] {len(bundles)} bundles found\n")

    for i, bundle in enumerate(bundles, 1):
        bid   = bundle["id"]
        bname = bundle.get("name", f"bundle_{bid}")
        print(f"[{i}/{len(bundles)}] Bundle: '{bname}'")
        process_bundle(bid, bname, completed, acc_completed)
        print()
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    print("=" * 60)
    print(f"Done! Saved to: {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()
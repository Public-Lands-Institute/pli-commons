#!/usr/bin/env python3
"""
upload_next.py — uploads up to 5 PLI images to Wikimedia Commons and exits.
Run via cron or manually. See pli-commons-CLAUDE.md for full documentation.

Usage:
  python3 upload_next.py                 # upload next 5 pending images
  python3 upload_next.py --edit-existing # update description pages of all logged uploads
"""

import json
import os
import re
import sys
import datetime
import requests
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS

Image.MAX_IMAGE_PIXELS = None  # disable decompression bomb limit for large TIFFs

# ── Paths ────────────────────────────────────────────────────────────────────

REPO_DIR     = Path(__file__).parent
PLI_DIR      = Path.home() / "PLI"
SITES_JSON   = PLI_DIR / "sites.json"
IMG_DIR_FULL = PLI_DIR / "img" / "full"
IMG_DIR_JPG  = PLI_DIR / "img" / "jpg"
LOG_FILE     = REPO_DIR / "upload_log.json"
ENV_FILE     = REPO_DIR / ".env"
INAT_CACHE   = PLI_DIR / "inaturalist_cache.json"

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
CHUNK_SIZE  = 5 * 1024 * 1024   # 5 MB
LARGE_FILE  = 10 * 1024 * 1024  # chunked upload threshold

# ── Lookups ───────────────────────────────────────────────────────────────────

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

# Ordered: more specific phrases before shorter ones to avoid partial shadowing
ECOLOGY_CATEGORY_MAP = [
    ("mixed mesophytic",            "Mixed mesophytic forests"),
    ("oak-hickory",                 "Oak-hickory forests"),
    ("hemlock-dominated",           "Hemlock forests"),
    ("canadian hemlock",            "Hemlock forests"),
    ("hemlock ravine",              "Hemlock forests"),
    ("floodplain forest",           "Floodplain forests"),
    ("bottomland hardwood",         "Bottomland hardwood forests"),
    ("riparian",                    "Riparian zones"),
    ("shortgrass prairie",          "Shortgrass prairies"),
    ("mixed-grass and shortgrass",  "Mixed grass prairies"),
    ("mixed-grass prairie",         "Mixed grass prairies"),
    ("tidal salt marsh",            "Salt marshes"),
    ("salt marsh",                  "Salt marshes"),
    ("wetland",                     "Wetlands"),
    ("cave ecosystem",              "Caves"),
    ("troglobitic",                 "Caves"),
    ("karst",                       "Karst landscapes"),
    ("granite outcrop",             "Granite landscapes"),
    ("granite",                     "Granite landscapes"),
    ("igneous glade",               "Rock outcrops"),
    ("rock shelter",                "Rock shelters"),
    ("sandstone",                   "Sandstone landscapes"),
    ("dolomite glade",              "Dolomite landscapes"),
    ("dolomite",                    "Dolomite landscapes"),
    ("canyon",                      "Canyon landscapes"),
    ("gorge",                       "Gorges"),
    ("savanna",                     "Savannas"),
    ("temperate deciduous",         "Temperate deciduous forests"),
    ("old-growth",                  "Old-growth forests"),
    ("migratory bird",              "Bird migration"),
    ("bird corridor",               "Bird migration"),
    ("bison",                       "American bison"),
    ("desert",                      "Desert landscapes"),
    ("prairie",                     "Prairies"),
    ("coastal",                     "Coastal landscapes"),
    ("beach",                       "Beaches"),
    ("cross timbers",               "Cross Timbers"),
    ("post oak savanna",            "Post oak savannas"),
    ("lichen",                      "Lichens"),
]

# ── Credential loading ────────────────────────────────────────────────────────

def load_env():
    if not ENV_FILE.exists():
        sys.exit(f"ERROR: .env file not found at {ENV_FILE}")
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

# ── Queue building ────────────────────────────────────────────────────────────

def build_queue(sites):
    """Return ordered list of queue item dicts.

    For each site, discovers all image stems in img/full/ (TIF) and img/jpg/ (JPG),
    sorted alphabetically. TIFF is preferred when both exist for the same stem.
    The Commons filename extension matches the source file.
    """
    queue = []
    for site in sites:
        slug     = site["slug"]
        full_dir = IMG_DIR_FULL / slug
        jpg_dir  = IMG_DIR_JPG  / slug

        stems: dict[str, Path] = {}
        if jpg_dir.is_dir():
            for p in jpg_dir.iterdir():
                if p.suffix.lower() == ".jpg":
                    stems[p.stem] = p
        if full_dir.is_dir():
            for p in full_dir.iterdir():
                if p.suffix.lower() == ".tif":
                    stems[p.stem] = p  # TIF overwrites JPG for same stem

        for idx, stem in enumerate(sorted(stems), start=1):
            src = stems[stem]
            ext = src.suffix.lower()
            commons_name = f"Public Lands Institute - {site['name']} - {idx:03d}{ext}"
            queue.append({
                "site": site,
                "source_path": src,
                "commons_filename": commons_name,
            })
    return queue

# ── Log I/O ───────────────────────────────────────────────────────────────────

def load_log():
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE) as f:
        return json.load(f)

def save_log(log):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)

# ── EXIF date extraction ──────────────────────────────────────────────────────

def get_image_date(path: Path) -> str:
    """Return ISO date string from EXIF DateTimeOriginal, or file mtime."""
    try:
        img = Image.open(path)
        exif_data = img._getexif()
        if exif_data:
            for tag_id, value in exif_data.items():
                if TAGS.get(tag_id) == "DateTimeOriginal":
                    return value.split(" ")[0].replace(":", "-")
    except Exception:
        pass
    mtime = path.stat().st_mtime
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

# ── Managing agency ───────────────────────────────────────────────────────────

def get_managing_agency(site) -> str:
    """Derive the managing agency from the conservation_status field."""
    status = site.get("conservation_status", "")
    sl = status.lower()

    if any(x in sl for x in ["national park", "national river and recreation",
                               "national seashore", "national monument",
                               "national recreation area"]):
        return "National Park Service"
    if "national wildlife refuge" in sl or "federal wilderness" in sl:
        return "U.S. Fish & Wildlife Service"
    if "texas state park" in sl:
        return "Texas Parks and Wildlife Department"
    if "missouri state park" in sl:
        return "Missouri Department of Natural Resources"
    if "ohio state park" in sl or "ohio state nature preserve" in sl:
        return "Ohio Department of Natural Resources"
    # Private / municipal — extract entity name from first parenthetical
    m = re.search(r'\(([^)]+)\)', status)
    if m:
        entity = m.group(1)
        # "City of Cincinnati / Great Parks of Hamilton County" → take the last part
        if "/" in entity:
            entity = entity.split("/")[-1].strip()
        return entity
    if "private nature preserve system" in sl:
        return site["name"]
    state = STATE_NAMES.get(site["state"], site["state"])
    return f"{state} land manager"

# ── Commons category lookup ───────────────────────────────────────────────────

def category_exists(session, category_name: str) -> bool:
    resp = session.get(COMMONS_API, params={
        "action": "query",
        "titles": f"Category:{category_name}",
        "format": "json",
    })
    pages = resp.json().get("query", {}).get("pages", {})
    return not any(pid == "-1" for pid in pages)

def find_site_category(session, site_name: str) -> str:
    """Search Commons for the best existing category for a site."""
    resp = session.get(COMMONS_API, params={
        "action": "query",
        "list": "search",
        "srnamespace": "14",
        "srsearch": site_name,
        "srlimit": "5",
        "format": "json",
    })
    results = resp.json().get("query", {}).get("search", [])
    if results:
        return results[0]["title"].replace("Category:", "")
    return site_name

def get_state_category(session, state_abbr: str) -> str:
    state_name = STATE_NAMES.get(state_abbr, state_abbr)
    candidate = f"Public lands in {state_name}"
    if category_exists(session, candidate):
        return candidate
    return f"Protected areas of {state_name}"

def get_ecology_categories(session, ecology_text: str) -> list[str]:
    """Map ecology description to verified Commons categories."""
    text_lower = ecology_text.lower()
    seen: set[str] = set()
    candidates = []
    for keyword, category in ECOLOGY_CATEGORY_MAP:
        if keyword in text_lower and category not in seen:
            seen.add(category)
            candidates.append(category)
    return [c for c in candidates if category_exists(session, c)]

# ── Structured data captions ─────────────────────────────────────────────────

def build_caption(site) -> str:
    state = STATE_NAMES.get(site["state"], site["state"])
    return f"Public Lands Institute photograph of {site['name']}, {state}."

def get_file_page_id(session, commons_filename: str):
    """Return the numeric page ID for a Commons file, or None if not found."""
    resp = session.get(COMMONS_API, params={
        "action": "query",
        "prop": "info",
        "titles": f"File:{commons_filename}",
        "format": "json",
    })
    pages = resp.json().get("query", {}).get("pages", {})
    for page_id, page in pages.items():
        if page_id != "-1":
            return int(page_id)
    return None

def set_file_caption(session, csrf_token: str, page_id: int, caption: str):
    """Set the English structured-data caption on a Commons MediaInfo entity."""
    r = session.post(COMMONS_API, data={
        "action": "wbeditentity",
        "id": f"M{page_id}",
        "data": json.dumps({"labels": {"en": {"language": "en", "value": caption}}}),
        "summary": "Adding English caption via PLI upload script",
        "token": csrf_token,
        "format": "json",
    })
    return r.json()

# ── Wikitext construction ─────────────────────────────────────────────────────

def build_wikitext(site, date_str: str, state_category: str,
                   site_category: str, ecology_categories: list[str],
                   native_lands: str = "", inat_species: list = None) -> str:
    name      = site["name"]
    state     = STATE_NAMES.get(site["state"], site["state"])
    lat       = site.get("lat", "")
    lng       = site.get("lng", "")
    geo_age   = site.get("geological_age", "").strip()
    epoch     = site.get("epoch", "").strip()
    hydrology = site.get("hydrology", "").strip()
    agency    = get_managing_agency(site)

    # First clause of hydrology (split on ; or ,)
    hydrology_short = re.split(r"[;]", hydrology)[0].strip() if hydrology else ""

    parts = [
        f"Photograph by the Public Lands Institute.",
        f"Site: {name}, {state}.",
        f"Managed by {agency}.",
    ]
    if geo_age:
        geo_str = f"{geo_age} ({epoch})" if epoch else geo_age
        parts.append(f"Geological age: {geo_str}.")
    if hydrology_short:
        parts.append(f"Hydrology: {hydrology_short}.")
    if native_lands:
        parts.append(f"Indigenous territories: {native_lands}.")
    if inat_species:
        parts.append(f"Notable species observed: {', '.join(inat_species)}.")

    description = " ".join(parts)

    location_line = f"{{{{Location|{lat}|{lng}}}}}" if lat and lng else ""

    categories = [
        "Public Lands Institute",
        "Public lands of the United States",
        state_category,
        site_category,
    ] + ecology_categories
    cat_lines = "\n".join(f"[[Category:{c}]]" for c in categories)

    wikitext = f"""=={{{{int:filedesc}}}}==
{{{{Information
|description={{{{en|1={description}}}}}
|date={date_str}
|source={{{{own}}}}
|author=[[User:Publiclandsinstitute|Public Lands Institute]]
|permission=
|other versions=
}}}}
{location_line}

=={{{{int:license-header}}}}==
{{{{CC-zero}}}}

{cat_lines}"""

    return wikitext.strip()

# ── MediaWiki API auth + upload ───────────────────────────────────────────────

def login(session, username, password):
    r = session.get(COMMONS_API, params={
        "action": "query", "meta": "tokens", "type": "login", "format": "json",
    })
    login_token = r.json()["query"]["tokens"]["logintoken"]
    r = session.post(COMMONS_API, data={
        "action": "login", "lgname": username, "lgpassword": password,
        "lgtoken": login_token, "format": "json",
    })
    if r.json().get("login", {}).get("result") != "Success":
        sys.exit(f"ERROR: Login failed — {r.json()}")

def get_csrf_token(session) -> str:
    r = session.get(COMMONS_API, params={
        "action": "query", "meta": "tokens", "format": "json",
    })
    return r.json()["query"]["tokens"]["csrftoken"]

def _mime(path: Path) -> str:
    return "image/tiff" if path.suffix.lower() == ".tif" else "image/jpeg"

def _upload_single(session, csrf_token, commons_filename, source_path, wikitext, comment):
    with open(source_path, "rb") as f:
        r = session.post(COMMONS_API, data={
            "action": "upload",
            "filename": commons_filename,
            "text": wikitext,
            "comment": comment,
            "ignorewarnings": "1",
            "token": csrf_token,
            "format": "json",
        }, files={"file": (commons_filename, f, _mime(source_path))})
    return r.json()

def _upload_chunked(session, csrf_token, commons_filename, source_path, wikitext, comment):
    file_size = source_path.stat().st_size
    mime = _mime(source_path)
    filekey = None
    offset = 0

    with open(source_path, "rb") as f:
        chunk_num = 0
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            data = {
                "action": "upload", "stash": "1",
                "filename": commons_filename,
                "filesize": str(file_size),
                "offset": str(offset),
                "ignorewarnings": "1",
                "token": csrf_token,
                "format": "json",
            }
            if filekey:
                data["filekey"] = filekey
            r = session.post(COMMONS_API, data=data,
                             files={"chunk": (commons_filename, chunk, mime)})
            result = r.json()
            if "error" in result:
                sys.exit(f"ERROR: Chunk {chunk_num} upload failed — {json.dumps(result, indent=2)}")
            upload = result.get("upload", {})
            filekey = upload.get("filekey", filekey)
            offset += len(chunk)
            chunk_num += 1
            print(f"        chunk {chunk_num}: {offset/1024/1024:.1f} / {file_size/1024/1024:.1f} MB")

    # Finalize: commit the stashed chunks
    finalize_data = {
        "action": "upload",
        "filename": commons_filename,
        "filekey": filekey,
        "text": wikitext,
        "comment": comment,
        "token": csrf_token,
        "format": "json",
    }
    r = session.post(COMMONS_API, data=finalize_data)
    result = r.json()
    # If the only warnings are non-content ones (deleted file, existing file),
    # retry with ignorewarnings so the full assembled file is committed.
    upload = result.get("upload", {})
    if upload.get("result") == "Warning":
        warnings = set(upload.get("warnings", {}).keys())
        if warnings <= {"was-deleted", "exists", "badfilename", "duplicate-archive", "nochange"}:
            finalize_data["ignorewarnings"] = "1"
            r = session.post(COMMONS_API, data=finalize_data)
            result = r.json()
    return result

def upload_file(session, csrf_token, commons_filename, source_path, wikitext, comment):
    if source_path.stat().st_size > LARGE_FILE:
        print(f"      Size:   {source_path.stat().st_size/1024/1024:.1f} MB — using chunked upload")
        return _upload_chunked(session, csrf_token, commons_filename, source_path, wikitext, comment)
    return _upload_single(session, csrf_token, commons_filename, source_path, wikitext, comment)

def edit_file_page(session, csrf_token, commons_filename, wikitext, summary):
    """Replace the description page of an already-uploaded Commons file."""
    r = session.post(COMMONS_API, data={
        "action": "edit",
        "title": f"File:{commons_filename}",
        "text": wikitext,
        "summary": summary,
        "token": csrf_token,
        "format": "json",
    })
    return r.json()

# ── Shared session setup ──────────────────────────────────────────────────────

def make_session(login_username, password) -> tuple:
    session = requests.Session()
    session.headers.update({"User-Agent": "PLICommonsUploader/1.0 (https://publiclandsinstitute.org)"})
    login(session, login_username, password)
    csrf_token = get_csrf_token(session)
    return session, csrf_token

def resolve_site_categories(session, site) -> tuple[str, str, list[str]]:
    """Return (state_category, site_category, ecology_categories) for a site."""
    state_cat   = get_state_category(session, site["state"])
    site_cat    = find_site_category(session, site["name"])
    ecology_cats = get_ecology_categories(session, site.get("ecology", ""))
    return state_cat, site_cat, ecology_cats

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_env()
    username = os.environ.get("COMMONS_USERNAME")
    bot_name = os.environ.get("COMMONS_BOT_NAME")
    password = os.environ.get("COMMONS_BOT_PASSWORD")
    if not username or not password:
        sys.exit("ERROR: COMMONS_USERNAME and COMMONS_BOT_PASSWORD must be set in .env")
    login_username = f"{username}@{bot_name}" if bot_name else username

    sites = json.loads(SITES_JSON.read_text())

    # Build slug -> top-species lookup from iNaturalist cache (if available)
    inat_by_slug: dict[str, list[str]] = {}
    if INAT_CACHE.exists():
        raw_cache = json.loads(INAT_CACHE.read_text())
        for key, val in raw_cache.items():
            slug_key = key.split(":")[0]
            species = [sp["name"] for sp in val.get("top_species", []) if sp.get("name")]
            if species:
                inat_by_slug[slug_key] = species

    edit_mode            = "--edit-existing"      in sys.argv
    deletion_mode        = "--request-deletion"   in sys.argv

    # ── Request deletion of all logged files ─────────────────────────────────
    if deletion_mode:
        log = load_log()
        if not log:
            print("No uploaded files in log.")
            return

        session, csrf_token = make_session(login_username, password)

        for i, entry in enumerate(log):
            commons_filename = entry["commons_filename"]
            print(f"[{i+1}/{len(log)}] Requesting deletion: {commons_filename}")
            r = session.post(COMMONS_API, data={
                "action": "edit",
                "title": f"File:{commons_filename}",
                "prependtext": "{{Db-author}}\n",
                "summary": "Requesting speedy deletion to re-upload with corrected metadata",
                "token": csrf_token,
                "format": "json",
            })
            result = r.json().get("edit", {}).get("result")
            if result != "Success":
                print(f"  ERROR: {r.json()}")
            else:
                print(f"  Tagged.")

        print(f"\nTagged {len(log)} file(s) for deletion. Re-upload once an admin deletes them.")
        return

    # ── Edit existing uploaded files ─────────────────────────────────────────
    if edit_mode:
        log = load_log()
        if not log:
            print("No uploaded files in log.")
            return

        session, csrf_token = make_session(login_username, password)
        sites_by_slug = {s["slug"]: s for s in sites}
        cache: dict[str, tuple] = {}  # slug -> (state_cat, site_cat, ecology_cats)

        for i, entry in enumerate(log):
            if entry.get("pending_deletion"):
                continue
            slug             = entry["slug"]
            commons_filename = entry["commons_filename"]
            source_path      = Path(entry["source_path"])

            print(f"\n[{i+1}/{len(log)}] Editing: {commons_filename}")

            site = sites_by_slug.get(slug)
            if not site:
                print(f"      WARNING: slug {slug!r} not in sites.json — skipping")
                continue

            if source_path.exists():
                date_str = get_image_date(source_path)
            else:
                date_str = entry.get("uploaded_at", "")[:10]

            if slug not in cache:
                print(f"      Resolving categories for {site['name']}...")
                cache[slug] = resolve_site_categories(session, site)
            state_cat, site_cat, ecology_cats = cache[slug]

            native_lands = site.get("native_lands", "")
            inat_species = inat_by_slug.get(slug, [])
            wikitext = build_wikitext(site, date_str, state_cat, site_cat, ecology_cats,
                                      native_lands=native_lands, inat_species=inat_species)
            state   = STATE_NAMES.get(site["state"], site["state"])
            summary = f"Public Lands Institute CC0 photograph — {site['name']}, {state}"
            response = edit_file_page(session, csrf_token, commons_filename, wikitext, summary)

            result = response.get("edit", {}).get("result")
            if result != "Success":
                print(f"      ERROR: {json.dumps(response, indent=2)}")
            else:
                page_id = get_file_page_id(session, commons_filename)
                if page_id:
                    caption = build_caption(site)
                    set_file_caption(session, csrf_token, page_id, caption)
                    print(f"      Caption: {caption}")
                encoded = commons_filename.replace(" ", "_")
                print(f"      Done:  https://commons.wikimedia.org/wiki/File:{encoded}")

        print(f"\nEdited {len(log)} file(s).")
        return

    # ── Upload next batch ─────────────────────────────────────────────────────
    queue = build_queue(sites)
    if not queue:
        print("No images found in PLI image directory.")
        return

    log = load_log()
    skip_filenames = {entry["commons_filename"] for entry in log}
    pending = [item for item in queue if item["commons_filename"] not in skip_filenames]

    if not pending:
        print("All images uploaded.")
        return

    session, csrf_token = make_session(login_username, password)
    cache: dict[str, tuple] = {}

    uploaded_count = 0
    for item in pending[:5]:
        site             = item["site"]
        source_path      = item["source_path"]
        commons_filename = item["commons_filename"]

        print(f"\n[{uploaded_count+1}/5] Uploading: {commons_filename}")
        print(f"      Source: {source_path}")

        # Skip if already on Commons (e.g. uploaded but not logged due to prior crash)
        if get_file_page_id(session, commons_filename):
            encoded     = commons_filename.replace(" ", "_")
            commons_url = f"https://commons.wikimedia.org/wiki/File:{encoded}"
            print(f"      Already on Commons — logging and skipping upload.")
            log.append({
                "commons_filename": commons_filename,
                "source_path": str(source_path),
                "slug": site["slug"],
                "site_name": site["name"],
                "uploaded_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "commons_url": commons_url,
            })
            save_log(log)
            uploaded_count += 1
            continue

        date_str = get_image_date(source_path)
        print(f"      Date:   {date_str}")

        slug = site["slug"]
        if slug not in cache:
            cache[slug] = resolve_site_categories(session, site)
        state_cat, site_cat, ecology_cats = cache[slug]

        native_lands = site.get("native_lands", "")
        inat_species = inat_by_slug.get(slug, [])
        wikitext = build_wikitext(site, date_str, state_cat, site_cat, ecology_cats,
                                  native_lands=native_lands, inat_species=inat_species)
        state   = STATE_NAMES.get(site["state"], site["state"])
        comment = f"Public Lands Institute CC0 photograph — {site['name']}, {state}"
        response = upload_file(session, csrf_token, commons_filename, source_path, wikitext, comment)

        upload_result = response.get("upload", {})
        warnings = set(upload_result.get("warnings", {}).keys())
        error_code = response.get("error", {}).get("code", "")
        nochange = (
            (upload_result.get("result") == "Warning" and warnings <= {"exists", "nochange"})
            or error_code == "fileexists-no-change"
        )
        if upload_result.get("result") != "Success" and not nochange:
            sys.exit(f"ERROR: Upload failed — {json.dumps(response, indent=2)}")
        if nochange:
            print(f"      Already on Commons (nochange) — logging and continuing.")

        encoded     = commons_filename.replace(" ", "_")
        commons_url = f"https://commons.wikimedia.org/wiki/File:{encoded}"

        log.append({
            "commons_filename": commons_filename,
            "source_path": str(source_path),
            "slug": slug,
            "site_name": site["name"],
            "uploaded_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commons_url": commons_url,
        })
        save_log(log)

        page_id = get_file_page_id(session, commons_filename)
        if page_id:
            caption = build_caption(site)
            set_file_caption(session, csrf_token, page_id, caption)
            print(f"      Caption: {caption}")
        print(f"      Done:   {commons_url}")
        uploaded_count += 1

    print(f"\nUploaded {uploaded_count} image(s). {len(pending) - uploaded_count} remaining.")

if __name__ == "__main__":
    main()

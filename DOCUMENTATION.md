# pli-commons

Automated upload system for contributing Public Lands Institute (PLI) photography to Wikimedia Commons under CC0 public domain.

## Relationship to PLI

This repo is a sibling to the main PLI site generator at ~/PLI/. It reads PLI source data but never modifies it. The PLI site and its generator are entirely separate and must not be touched by any script in this repo.

Source data used (read-only):
- ~/PLI/sites.json — site metadata including name, slug, state, lat, lng, geology, ecology, native_lands, etc.
- ~/PLI/img/jpg/<slug>/ — source images for each site, named alphabetically, displayed on PLI with Roman numeral captions (I, II, III...)

## What This Repo Does

Reads PLI source images and metadata, creates renamed copies, and uploads them to Wikimedia Commons one image per day via the MediaWiki API. Tracks upload state in a local log so it never uploads the same image twice and always picks up where it left off.

## Credentials

Stored in .env (never commit this file):

```
COMMONS_USERNAME=Publiclandsinstitute
COMMONS_BOT_PASSWORD=<bot password from Special:BotPasswords>
```

To generate a bot password:
1. Log in to commons.wikimedia.org as Publiclandsinstitute
2. Go to Special:BotPasswords
3. Create a new bot password with permissions: Edit existing pages, Create, edit, and move pages, Upload new files, Upload, replace, and move files
4. Store the generated password in .env as COMMONS_BOT_PASSWORD

## File Naming Convention

Commons filenames must be unique, descriptive, and human-readable. PLI images are renamed on upload using this pattern:

`Public Lands Institute - {Site Name} - {###}.jpg`

Where {###} is a zero-padded sequential number (001, 002, 003...) corresponding to the alphabetical order of source images within a site (matching the Roman numeral order on the PLI site).

Examples:
- `Public Lands Institute - Mammoth Cave National Park - 001.jpg`
- `Public Lands Institute - Mammoth Cave National Park - 002.jpg`
- `Public Lands Institute - Big South Fork NRRA (Tennessee) - 001.jpg`

## Image Metadata Template

Each uploaded image gets the following Commons file page content:

```
=={{int:filedesc}}==
{{Information
|description={{en|1={DESCRIPTION}}}
|date={EXIF_DATE or FILE_DATE}
|source={{own}}
|author=[[User:Publiclandsinstitute|Jordan Tate]]
|permission=
|other versions=
}}
{{Location|{LAT}|{LNG}}}

=={{int:license-header}}==
{{CC-zero}}

[[Category:Public Lands Institute]]
[[Category:Public lands of the United States]]
[[Category:{STATE_CATEGORY}]]
[[Category:{SITE_CATEGORY}]]
```

### Description format

`Photograph from the Public Lands Institute, a CC0 public domain photographic index of American public lands. Site: {Site Name}, {State}. {One sentence from ecology or geology field in sites.json if available}.`

### Categories

Every image gets:
- `Public Lands Institute` (create this category if it does not exist)
- `Public lands of the United States`
- State-level category: e.g. `Public lands in Kentucky`, `Public lands in Tennessee` — check if the category exists on Commons before using it; use the closest existing equivalent if not
- Site-level category: search Commons for an existing category for the specific park or preserve; use the most specific one available

## Upload Queue and State Tracking

Upload state is tracked in `upload_log.json` in this repo (do not commit this file, add to .gitignore):

```json
[
  {
    "commons_filename": "Public Lands Institute - Mammoth Cave National Park - 001.jpg",
    "source_path": "/Users/.../PLI/img/jpg/mammoth-cave-national-park/filename.jpg",
    "slug": "mammoth-cave-national-park",
    "site_name": "Mammoth Cave National Park",
    "uploaded_at": "2026-03-18T10:00:00Z",
    "commons_url": "https://commons.wikimedia.org/wiki/File:Public_Lands_Institute_-_Mammoth_Cave_National_Park_-_001.jpg"
  }
]
```

The upload script builds a full queue from sites.json + image directories at runtime, cross-references upload_log.json to find unuploaded images, and uploads the next one in queue order (sites in sites.json order, images alphabetically within each site).

## Daily Upload Script

`upload_next.py` — uploads one image and exits. Run via cron or manually.

Workflow:
1. Load sites.json and build full image queue
2. Load upload_log.json (create empty if missing)
3. Find first queue item not in log
4. If none found: print "All images uploaded" and exit
5. Extract EXIF date from source image (fall back to file modification date)
6. Look up site metadata from sites.json
7. Construct Commons filename and file page wikitext
8. Authenticate to MediaWiki API using bot password
9. Upload file
10. Append result to upload_log.json
11. Print confirmation with Commons URL

## Cron Setup

Run once daily from Mac Terminal. Add to crontab:

```
0 9 * * * cd /path/to/pli-commons && python3 upload_next.py >> upload.log 2>&1
```

Adjust path to match actual repo location.

## Dependencies

- Python 3
- Pillow (for EXIF extraction): `pip3 install Pillow --break-system-packages`
- requests: `pip3 install requests --break-system-packages`

## What Not to Do

- Never modify files in ~/PLI/
- Never upload duplicate filenames to Commons (check log before uploading)
- Never commit .env or upload_log.json
- Never upload more than one image per script run (Commons rate limiting; also builds contribution history gradually which is important for the Wikimedia Rapid Fund application)
- Never delete or overwrite source images

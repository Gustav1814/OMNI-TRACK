"""Fetch the public evaluation datasets into backend/storage/datasets/<name>/ (gitignored).

    python eval/fetch_datasets.py fer2013      # FER-2013 public test split, 4 shards, ~39 MB
    python eval/fetch_datasets.py market1501   # Market-1501 v15.09.15, ~110 MB
    python eval/fetch_datasets.py dfire        # D-Fire test split, frozen 1-in-3 subset, ~200 MB
    python eval/fetch_datasets.py all

Sources are pinned to a Hugging Face revision and every file is sha256-checked.
The CAVIAR clips and the Mall dataset are already under backend/storage/demo_videos/.
Licences: FER-2013 (ICML 2013 challenge, research use), Market-1501 (research
only, do not redistribute), D-Fire (CC0 per the authors' GitHub repository).
"""

from __future__ import annotations

import json
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

from common import DATASETS_DIR, download, log, sha256_file

HF = "https://huggingface.co/datasets"

FER = {
    "repo": "clip-benchmark/wds_fer2013",
    "revision": "9399b94167523fe5c40b3a857e24ef931ee4395b",
    "shards": {
        "test/0.tar": "93ad8bd1ee8d7c931ab1a1372ac56dd5b4ea65dbaac19acd42ef149bfbdccbb8",
        "test/1.tar": "f3f57b2079c42d1c94da4688fd8313e150f146f704f73798c1d57880c4fd2006",
        "test/2.tar": "17ee8ee8c2721fa95deb24c04045592f76f1d8337e4812ff11301986ab47372c",
        "test/3.tar": "3df05541304fbdb05baefcac6782762e95da58dbdefdce328b28ef8ca25ecde7",
    },
    # wds_fer2013 class index -> name (classnames.txt of the same revision)
    "classes": ["angry", "disgusted", "fearful", "happy", "neutral", "sad", "surprised"],
}

MARKET = {
    "repo": "Lekim89/market1501",
    "revision": "c9782d6d810364cd0d344a6bfe01d459713eeb51",
    "file": "Market-1501-v15.09.15.tar.gz",
    "sha256": "e6604a8a0f9663e5ac366387d481b201b614c66d29229c76cde6afa357742b96",
}

DFIRE = {
    "repo": "badsaarow/d-fire",
    "revision": "27f81e6f7d32fe3b29d366da9573b6150a3d148f",
    "rows_api": "https://datasets-server.huggingface.co/rows",
    "every": 3,  # frozen 1-in-3 subset of the 4,306-image test split (keeps the download ~200 MB)
    "total": 4306,
}


def dfire_expected() -> int:
    """Images in the complete frozen D-Fire subset (fire.py refuses a partial download)."""
    return len(range(0, DFIRE["total"], DFIRE["every"]))


def fer2013() -> Path:
    root = DATASETS_DIR / "fer2013"
    for shard, digest in FER["shards"].items():
        download(f"{HF}/{FER['repo']}/resolve/{FER['revision']}/{shard}", root / shard, digest)
    log(f"FER-2013 test shards ready in {root}")
    return root


def market1501() -> Path:
    root = DATASETS_DIR / "market1501"
    final = root / "Market-1501-v15.09.15"
    if (final / "query").is_dir() and (final / "bounding_box_test").is_dir():
        return final
    archive = download(f"{HF}/{MARKET['repo']}/resolve/{MARKET['revision']}/{MARKET['file']}",
                       root / MARKET["file"], MARKET["sha256"])
    log("extracting Market-1501 (query + bounding_box_test only)")
    with tarfile.open(archive, "r:gz") as tf:
        members = [m for m in tf.getmembers()
                   if m.isfile() and any(f"/{d}/" in f"/{m.name}" for d in ("query", "bounding_box_test"))]
        tf.extractall(root, members=members, filter="data")
    hits = list(root.rglob("query"))
    if hits and hits[0].parent != final:
        hits[0].parent.rename(final)
    archive.unlink()
    return final


def _rows(offset: int, length: int) -> list[dict]:
    url = (f"{DFIRE['rows_api']}?dataset={DFIRE['repo']}&config=default&split=test"
           f"&offset={offset}&length={length}")
    for attempt in range(10):  # the datasets-server returns bursts of 5xx
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "omnitrack-eval/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["rows"]
        except Exception as exc:  # noqa: BLE001 - transient API errors
            log(f"rows API retry {attempt + 1}: {exc}")
            time.sleep(min(60, 5 * (attempt + 1)))
    raise SystemExit("datasets-server rows API unavailable")


def dfire() -> Path:
    """Download every DFIRE['every']-th test image with its YOLO label (0 = smoke, 1 = fire)."""
    root = DATASETS_DIR / "dfire" / "test"
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    index_path = root / "index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {}
    total, page = DFIRE["total"], 100
    for off in range(0, total, page):
        wanted = [i for i in range(off, min(off + page, total)) if i % DFIRE["every"] == 0]
        if all(str(i) in index for i in wanted):
            continue
        for row in _rows(off, page):
            i = row["row_idx"]
            if i % DFIRE["every"] or str(i) in index:
                continue
            r = row["row"]
            name = r["filename"]
            img = root / "images" / name
            download(r["image"]["src"], img)
            (root / "labels" / Path(name).with_suffix(".txt")).write_text(r["label"] or "")
            index[str(i)] = {"file": name, "sha256": sha256_file(img), "label": r["label"] or ""}
        index_path.write_text(json.dumps(index, indent=0))
        log(f"D-Fire: {len(index)} images")
    return root


if __name__ == "__main__":
    which = sys.argv[1:] or ["all"]
    for name in (["fer2013", "market1501", "dfire"] if "all" in which else which):
        print(globals()[name]())

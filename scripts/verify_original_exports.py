"""Independently decode exported MP3s and compare original bytes with the DB."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

import miniaudio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--databases", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.export / "manifest.json").read_text(encoding="utf-8"))
    reports = []
    for index, item in enumerate(manifest["exported"], 1):
        original = args.export / item["original_file"]
        mp3 = args.export / item["mp3_file"]
        database = args.databases / "message" / item["media_database"]
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT voice_data FROM VoiceInfo WHERE rowid=?", (item["media_rowid"],)).fetchone()
        matches = row is not None and hashlib.sha256(row[0]).hexdigest() == item["original_sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
        # miniaudio's Windows C fopen does not accept all Unicode paths.
        # Python opens the file; the independent decoder consumes actual bytes.
        data = mp3.read_bytes()
        decoded = miniaudio.decode(data, nchannels=1, sample_rate=24000)
        duration = len(decoded.samples) / 24000
        peak = max((abs(int(value)) for value in decoded.samples), default=0)
        mp3_hash_matches = hashlib.sha256(data).hexdigest() == item["mp3_sha256"]
        valid = matches and mp3_hash_matches and abs(duration - item["decoded_seconds"]) < 0.25
        reports.append({"sample": index, "original_matches_database": matches,
                        "mp3_decodable": True, "mp3_hash_matches": mp3_hash_matches,
                        "source_seconds": item["decoded_seconds"], "mp3_seconds": round(duration, 3),
                        "nonzero_audio": peak > 0, "bytes": len(data),
                        "metadata_match": item["association"], "passed": valid})
    report = {"recording_used": False, "samples": reports, "passed": bool(reports) and all(item["passed"] for item in reports)}
    (args.export / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://www.mutopiaproject.org/ftp/JoplinS/maple/maple.mid"
SOURCE_PAGE = "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=23"
TARGET = Path(__file__).with_name("maple-leaf-rag.mid")
SOURCE_RECORD = Path(__file__).with_name("maple-leaf-rag-SOURCE.txt")


def main() -> int:
    request = Request(URL, headers={"User-Agent": "MIDI-Performer/0.7.0 sample helper"})
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read()
    except Exception as exc:
        print(f"Download failed: {exc}")
        print(f"Open the source page manually: {SOURCE_PAGE}")
        return 1
    if not data.startswith(b"MThd"):
        print("Download did not contain a valid Standard MIDI File header.")
        return 2
    TARGET.write_bytes(data)
    digest = sha256(data).hexdigest()
    SOURCE_RECORD.write_text(
        "\n".join(
            [
                "Maple Leaf Rag sample source record",
                f"Retrieved: {date.today().isoformat()}",
                f"Source page: {SOURCE_PAGE}",
                f"Direct file: {URL}",
                "Mutopia ID: Mutopia-2011/11/13-23",
                "Status stated by Mutopia: Public Domain, CC No Rights Reserved",
                f"SHA-256: {digest}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Saved {TARGET} ({len(data)} bytes)")
    print(f"SHA-256: {digest}")
    print(f"Source record: {SOURCE_RECORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

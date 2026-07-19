from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOUNDFONTS = ROOT / "assets" / "soundfonts"
SOUNDFONT_URL = "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2"
LICENSE_URL = "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/documentation/LICENSE.txt"

def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "MIDI-Performer/0.7.0"})
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as out:
        while True:
            chunk = response.read(1024 * 512)
            if not chunk:
                break
            out.write(chunk)

def main() -> None:
    sf2 = SOUNDFONTS / "GeneralUser-GS.sf2"
    license_file = SOUNDFONTS / "GeneralUser-GS-LICENSE.txt"
    download(SOUNDFONT_URL, sf2)
    download(LICENSE_URL, license_file)
    print(sf2)

if __name__ == "__main__":
    main()

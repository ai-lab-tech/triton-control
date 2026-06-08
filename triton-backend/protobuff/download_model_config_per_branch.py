import argparse
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="triton-inference-server/common")
    parser.add_argument("--prefix", default="r", help="z. B. r24.11")
    parser.add_argument("--out", default=".")
    args = parser.parse_args()

    source = f"https://github.com/{args.repo}.git"
    result = subprocess.run(
        ["git", "ls-remote", "--heads", source],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[error] Konnte Branches nicht laden")
        return 1

    branches = []
    for line in result.stdout.splitlines():
        ref = line.split("\t")[-1]
        if ref.startswith("refs/heads/"):
            name = ref.replace("refs/heads/", "", 1)
            if name.startswith(args.prefix):
                branches.append(name)

    if not branches:
        print("[info] Keine passenden Branches gefunden")
        return 0

    out_root = Path(args.out)

    for branch in sorted(branches):
        target = out_root / branch / "model_config.proto"
        if target.exists() and target.stat().st_size > 0:
            print(f"[skip] {branch}")
            continue

        url = f"https://raw.githubusercontent.com/{args.repo}/{branch}/protobuf/model_config.proto"

        try:
            with urlopen(url, timeout=30) as response: 
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(response.read().decode("utf-8"), encoding="utf-8")
            print(f"[ok]   {branch}")
        except HTTPError as exc:
            if exc.code == 404:
                print(f"[ignore] {branch}: kein model_config.proto")
                continue
            print(f"[fail] {branch}: HTTP {exc.code}")
            return 2
        except URLError as exc:
            print(f"[fail] {branch}: {exc.reason}")
            return 2

    print("[done]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

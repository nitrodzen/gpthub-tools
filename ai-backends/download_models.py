"""Download pinned model files; verify SHA-256 before atomically installing them."""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--group', choices=('upscale', 'cpu'), default='upscale')
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    models = json.loads(Path(__file__).with_name('models.json').read_text(encoding='utf-8-sig'))
    for model in models:
        is_background = model['file'].startswith('birefnet-')
        if model['file'] != 'clean.onnx' and is_background != (args.group == 'cpu'):
            continue
        target = args.destination / model['file']
        if target.exists() and digest(target) == model['sha256']:
            print(f'Verified {target.name}', flush=True)
            continue
        temporary = target.with_suffix(target.suffix + '.download')
        print(f'Downloading {target.name}', flush=True)
        try:
            urllib.request.urlretrieve(model['source'], temporary)
            if digest(temporary) != model['sha256']:
                raise RuntimeError(f'Checksum mismatch: {target.name}')
            temporary.replace(target)
            target.chmod(0o644)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()

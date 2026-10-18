"""Generate synthetic (non-personal) bundles for on-device importer checks."""
from pathlib import Path
import sys
import tempfile
import zipfile
import json
import argparse

sys.path.insert(0,str(Path(__file__).parents[1]/'tests'))
from test_voice_library import library
from mobile_package import create_package


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).parents[1]/'build/android-test-fixtures-v2')
    target=parser.parse_args().output
    target.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        data=library.__wrapped__(Path(directory))
        existing=target/'valid.voicepack.zip'
        if existing.exists():
            raise FileExistsError('Use a new fixtures directory or retain the existing generated fixtures.')
        create_package(data,existing,data.records)
        with zipfile.ZipFile(target/'valid.voicepack.zip') as valid:
            for name,corrupt in [('corrupt.voicepack.zip',True),('traversal.voicepack.zip',False)]:
                with zipfile.ZipFile(target/name,'w') as bundle:
                    for entry in valid.namelist():
                        content=valid.read(entry)
                        if corrupt and entry.endswith('.mp3'):content=b'broken bytes'
                        bundle.writestr(entry,content)
                    if not corrupt:bundle.writestr('../escape.mp3',b'bad')
            with zipfile.ZipFile(target/'conflict.voicepack.zip','w') as conflict:
                for entry in valid.namelist():
                    content=valid.read(entry)
                    if entry=='manifest.json':
                        manifest=json.loads(content)
                        manifest['voices'][0]['timestamp']+=1
                        content=json.dumps(manifest,ensure_ascii=False).encode()
                    conflict.writestr(entry,content)
    print('Synthetic Android fixtures ready.')


if __name__=='__main__': main()

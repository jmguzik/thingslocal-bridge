#!/usr/bin/env python3
"""Create public source/add-on artifacts from an explicit, secret-free file list."""
import argparse
from pathlib import Path
import re
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def public_files():
    fixed = ('pyproject.toml', 'README.md', 'UPSTREAM.md', 'LICENSE', 'CHANGELOG.md', 'repository.yaml', '.gitignore',
             'thingslocal_bridge/config.yaml', 'thingslocal_bridge/Dockerfile',
             'thingslocal_bridge/requirements.txt', 'thingslocal_bridge/LICENSE',
             'thingslocal_bridge/UPSTREAM.md', 'thingslocal_bridge/README.md',
             'thingslocal_bridge/DOCS.md', 'thingslocal_bridge/CHANGELOG.md',
             'thingslocal_bridge/.dockerignore')
    paths = [ROOT/name for name in fixed]
    for folder, suffixes in (('thingslocal_bridge/src', ('.py', '.pem', '.cjs')),
                             ('tests', ('.py',)), ('docs', ('.md',)), ('tools', ('.py',)),
                             ('.github/workflows', ('.yml', '.yaml')), ('blueprints', ('.yaml',))):
        paths.extend(path for path in (ROOT/folder).rglob('*') if path.is_file()
                     and path.suffix in suffixes and '__pycache__' not in path.parts)
    for path in sorted(set(paths)):
        if not path.is_file() or path.is_symlink(): raise ValueError('Missing or unsafe public source file: '+str(path))
        data = path.read_bytes()
        if re.search(rb'^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', data, re.M):
            raise ValueError('A private key was found in the public source tree')
        yield path


def write_artifact(output, addon_only=False):
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    paths=list(public_files())
    if addon_only:
        with tarfile.open(output,'w:gz') as archive:
            for path in paths:
                relative=path.relative_to(ROOT)
                if relative.parts[0]=='thingslocal_bridge':archive.add(path,arcname=str(relative),recursive=False)
    else:
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in paths:archive.write(path,str(Path('thingslocal-bridge')/path.relative_to(ROOT)))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--addon-only',action='store_true');args=parser.parse_args()
    print(write_artifact(args.output,args.addon_only))

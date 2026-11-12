"""Count the mesh libraries in a shader library: a package's msl folder or its b-<chain>.img.

    python scripts/check_mesh_libs.py "<path to ...\\msl>"      (a folder holding b-<chain>/ or b-<chain>.img)
    python scripts/check_mesh_libs.py "<path to b-<chain>.img>"

Prints what the library holds, which is the question the host's own line answers on the phone ("has a mesh library" /
"has no mesh library (geo_mesh.tsv): the compute path draws it"). A geo_mesh game without them runs the compute path
for those passes, which hung NieR:Automata (issue #14). Read-only, and the count the packer's own log line gives.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'dm' / 'tools'))
import romfs, shader_lib                                                   # noqa: E402


def entries(image):
    im = romfs.Image(str(image))
    return [im.name(k).decode('utf-8', 'replace') for k in range(im.count) if im.entry(k)[4]]


def report(label, names):
    mesh = [n for n in names if n.endswith('_m3.metallib')]
    libs = [n for n in names if n.endswith('_b.metallib')]
    print('%-38s %5d entries: %d libraries, %d mesh libraries' % (label, len(names), len(libs), len(mesh)))
    for n in mesh[:4]:
        print('    ' + n)
    return len(mesh)


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    p = Path(argv[1])
    if not p.exists():
        sys.exit('[x] no such path: %s' % p)
    total = 0
    if p.is_file():
        total = report(p.name, entries(p))
    else:
        imgs = sorted(p.glob('b-*.img'))
        dirs = sorted(d for d in p.glob('b-*') if d.is_dir())
        for d in dirs:
            total += report(d.name + '/', [x.name for x in d.iterdir() if x.is_file()])
        for i in imgs:
            total += report(i.name, entries(i))
        if not imgs and not dirs:
            sys.exit('[x] %s holds neither b-<chain>/ nor b-<chain>.img' % p)
    print()
    print('mesh libraries found: %d' % total)
    print('0 = this package cannot draw a geo_mesh pair with a mesh pipeline, so the host takes the compute path')
    print('96 = NieR:Automata (0100B8E016F76000 and 010056B015FE8000) complete; 1 = Donkey Kong Bananza (040074A01BF12000)')


if __name__ == '__main__':
    main(sys.argv)

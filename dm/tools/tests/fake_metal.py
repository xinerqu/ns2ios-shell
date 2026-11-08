#!/usr/bin/env python3
# A stand-in for Apple's `metal` / `metallib`, so the hook in dm/gfx/tools/nometal_run.py can be tested on a machine that has
# neither (tests/test_metal_tools_hook.py). It is not part of any package and nothing outside the test imports it.
#
#   fake_metal.py metal -c -O1 <source> -o <air>     writes the source bytes, and a marker beside the output
#   fake_metal.py metal --version                    prints a metal-looking version
#   fake_metal.py metallib <air> -o <library>        writes a small but valid Metal library listing the entry points it finds
#                                                    in its input; FAKE_METALLIB=garbage writes text instead (a tool that
#                                                    exits 0 but produces something this route cannot read)
#   fake_metal.py metal-fail ...                     exits 1, as a compiler that cannot handle the shader would
import os, re, struct, sys

ENTRY = re.compile(r'^\s*(?:vertex|fragment|kernel)\s+[\w<>,\s\*]+?\s(\w+)\s*\(', re.M)


def library(names):
    """A Metal library with this header layout: 'MTLB', the file's size at 0x10, the function list's offset at 0x18, then the
    count and one size-prefixed tag list per function (NAME, ENDT) -- what pso_offline.functions and dmsh_side.cut read."""
    ents = b''
    for n in names:
        name = n.encode() + b'\0'
        body = b'NAME' + struct.pack('<H', len(name)) + name + b'ENDT'
        ents += struct.pack('<I', len(body) + 4) + body
    listing = struct.pack('<I', len(names)) + ents
    fo = 0x58
    head = bytearray(fo)
    head[0:4] = b'MTLB'
    struct.pack_into('<Q', head, 0x10, fo + len(listing))     # the file's size
    struct.pack_into('<Q', head, 0x18, fo)                    # function list
    struct.pack_into('<Q', head, 0x20, 0)                     # public metadata
    struct.pack_into('<Q', head, 0x28, 0)
    struct.pack_into('<Q', head, 0x30, 0)
    return bytes(head) + listing


def main(argv):
    mode = os.environ.get('FAKE_METAL', '')
    if not argv: return 2
    prog, args = argv[0], argv[1:]
    if prog == 'metal' and '--version' in args:
        print('Apple metal version 32023.828 (metalfe-32023.828.4)'); return 0
    if prog == 'metallib' and '--version' in args:
        print('Apple metallib version 32023.828'); return 0
    if mode == 'fail' or prog == 'metal-fail':
        sys.stderr.write('fake_metal: this compiler cannot handle that shader\n'); return 1
    out = args[args.index('-o') + 1] if '-o' in args else None
    if out is None: return 2
    if prog == 'metal':
        src = [a for a in args if a.endswith('.metal')]
        if src:
            with open(src[0], 'rb') as f: b = f.read()
        else:                                    # the source came on standard input
            b = sys.stdin.buffer.read()
        with open(out, 'wb') as f: f.write(b.replace(b'\r\n', b'\n'))
        with open(out + '.called', 'w') as f: f.write('metal\n')
        return 0
    # metallib: a library listing the entry points of what it was given
    src = [a for a in args[:args.index('-o')] if not a.startswith('-')]
    names = ['main0']
    if src and os.path.isfile(src[0]):
        with open(src[0], 'rb') as f: text = f.read().decode('utf-8', 'replace')
        found = ENTRY.findall(text)
        if found: names = found
    if os.environ.get('FAKE_METALLIB') == 'garbage':
        with open(out, 'wb') as f: f.write(b'this is not a library\n')
    else:
        with open(out, 'wb') as f: f.write(library(names))
    with open(out + '.called', 'w') as f: f.write('metallib\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

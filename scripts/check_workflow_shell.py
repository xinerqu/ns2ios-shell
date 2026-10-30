# -*- coding: utf-8 -*-
"""bash -n every `run: |` block in a workflow, so a quoting slip is caught here and not on the runner.

Why: a workflow's shell is only parsed when the runner reaches it, and a stray backslash inside a quoted echo makes bash
join the following lines -- the step then dies on a comment from twenty lines below, which reads as "git hash-object is
broken" rather than "the line above it has one character too many". Two runs (2026-10-30) went to that.
GitHub's own parser is not the thing to ask: it accepts any text in a block scalar.

    python scripts/check_workflow_shell.py [.github/workflows/shell.yml]
"""
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[1]
path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / '.github' / 'workflows' / 'shell.yml'
lines = path.read_text(encoding='utf-8').splitlines()

blocks, i = [], 0
while i < len(lines):
    m = re.match(r'^(\s*)(?:-\s+)?run:\s*\|-?\s*$', lines[i])
    if m:
        base = len(m.group(1))
        j = i + 1
        body = []
        while j < len(lines):
            line = lines[j]
            if line.strip() and (len(line) - len(line.lstrip())) <= base:
                break
            body.append(line)
            j += 1
        first = next((k for k, l in enumerate(body) if l.strip()), 0)
        pad = len(body[first]) - len(body[first].lstrip()) if body else 0
        blocks.append((i + 1, [l[pad:] if len(l) >= pad else l for l in body]))
        i = j
    else:
        i += 1

bad = 0
for line_no, body in blocks:
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False, encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(body) + '\n')
        name = f.name
    r = subprocess.run(['bash', '-n', name], capture_output=True, text=True)
    if r.returncode:
        bad += 1
        print('FAIL block at line %d: %s' % (line_no, (r.stderr or '').strip().splitlines()[0] if r.stderr else ''))
        for l in (r.stderr or '').strip().splitlines()[1:4]:
            print('    ' + l)
    pathlib.Path(name).unlink()
print('%d block(s), %d with a syntax error' % (len(blocks), bad))
sys.exit(1 if bad else 0)

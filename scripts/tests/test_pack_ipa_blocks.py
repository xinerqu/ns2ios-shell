"""A big file is deflated a block at a time by the pool, and the blocks still read back as one deflate stream.

Why: `pack_ipa.py` streams a file over 256 MB on the writer's own core (`stream`), so one `romfs.img` decides the whole
pack. Measured on Tears of the Kingdom (15.88 GiB image, 16 cores, 2026-11-06): 432.2 s on one core, 40.2 s in 16 MB
blocks through the pool, archives 99 KB apart (+0.0006%). A deflate stream is a row of blocks, so each block may be
compressed on another core -- but only if it *ends* with a full flush (byte boundary, empty window) and the last one
ends the stream instead. Get that wrong and the pack still finishes, still reports a size, and no reader can inflate it:
the failure is not in the timing, which is why it is worth pinning here.

`test_the_blocks_come_out_of_the_pool` fails if a later edit quietly drops the pool argument again (the pack would just
be slow, never wrong -- the kind of regression nothing else notices).
"""
import importlib.util
import os
import pathlib
import tempfile
import unittest
import zipfile
import zlib

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
_spec = importlib.util.spec_from_file_location('pack_ipa', REPO / 'dm' / 'ios' / 'tools' / 'pack_ipa.py')
pk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pk)


def payload(size):
    """Compressible, and not the same byte everywhere: a bad block boundary shows up as wrong bytes, not as a size."""
    out, i = bytearray(), 0
    while len(out) < size:
        out += (f'block {i} '.encode() * 900) + os.urandom(64)
        i += 1
    return bytes(out[:size])


class TheBlocksMakeOneStream(unittest.TestCase):
    def test_blocks_concatenated_inflate_to_the_input(self):
        data = payload(3 * pk.CHUNK + 1234)         # four blocks, the last one short
        n = (len(data) + pk.CHUNK - 1) // pk.CHUNK
        blob = b''.join(pk.deflate_block(data[i * pk.CHUNK:(i + 1) * pk.CHUNK], i == n - 1) for i in range(n))
        d = zlib.decompressobj(-15)
        self.assertEqual(d.decompress(blob) + d.flush(), data)
        self.assertTrue(d.eof, 'the stream has no end-of-stream marker')

    def test_a_full_flush_on_the_last_block_breaks_the_end(self):
        # The failure the comment in deflate_block warns about: same bytes, no end marker, and a reader that waits for one.
        blob = pk.deflate_block(payload(pk.CHUNK), False)
        d = zlib.decompressobj(-15)
        d.decompress(blob); d.flush()
        self.assertFalse(d.eof)


class TheBigFileGoesThroughThePool(unittest.TestCase):
    def test_the_blocks_come_out_of_the_pool(self):
        import concurrent.futures as cf
        data = payload(2 * pk.CHUNK + 777)
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp, 'big.bin'); src.write_bytes(data)
            e = pk.Entry('Payload/X.app/big.bin', str(src), os.stat(src), tmp)
            out = pathlib.Path(tmp, 'x.ipa')
            z = pk.Zip(str(out), 0)
            with cf.ThreadPoolExecutor(os.cpu_count()) as pool:
                z.stream(e, True, pool)             # the caller main() makes for a file over BIG
            z.close()
            self.assertGreater(z.stats['blocks'], 1, 'stream() did not use the pool: the file went through one core')
            with zipfile.ZipFile(out) as archive:
                self.assertIsNone(archive.testzip(), 'the archive fails its own CRC test')
                self.assertEqual(archive.read('Payload/X.app/big.bin'), data)

    def test_a_file_that_does_not_shrink_is_stored_whole(self):
        # Incompressible input: the parallel path still has to fall back to store, and the entry must read back the same.
        data = os.urandom(2 * pk.CHUNK + 5)
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp, 'noise.bin'); src.write_bytes(data)
            e = pk.Entry('Payload/X.app/noise.bin', str(src), os.stat(src), tmp)
            out = pathlib.Path(tmp, 'y.ipa')
            z = pk.Zip(str(out), 0)
            import concurrent.futures as cf
            with cf.ThreadPoolExecutor(os.cpu_count()) as pool:
                z.stream(e, True, pool)
            z.close()
            with zipfile.ZipFile(out) as archive:
                info = archive.getinfo('Payload/X.app/noise.bin')
                self.assertEqual(info.compress_type, 0, 'deflate grew it and it was not stored')
                self.assertEqual(archive.read(info.filename), data)


if __name__ == '__main__':
    unittest.main()

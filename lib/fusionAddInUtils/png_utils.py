import struct
import zlib

# Minimal PNG read / write / downscale -- Fusion's bundled Python has no PIL. Handles what
# Fusion's own thumbnails are: 8-bit greyscale / grey+alpha / RGB / RGBA, non-interlaced.

_PNG_SIG = b'\x89PNG\r\n\x1a\n'
_CHANNELS = {0: 1, 4: 2, 2: 3, 6: 4}   # PNG colour type -> samples per pixel


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path: str):
    """(width, height, channels, rows) for an 8-bit non-interlaced PNG; each row a bytearray
    of width * channels samples. Raises ValueError for anything else."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:8] != _PNG_SIG:
        raise ValueError(f'{path} is not a PNG')
    pos, idat, header = 8, [], None
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', body)
        elif kind == b'IDAT':
            idat.append(body)
        elif kind == b'IEND':
            break
    if header is None:
        raise ValueError(f'{path} has no IHDR')
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour not in _CHANNELS or interlace:
        raise ValueError(f'{path}: unsupported PNG (depth {depth}, colour {colour}, '
                         f'interlace {interlace})')
    channels = _CHANNELS[colour]
    raw = zlib.decompress(b''.join(idat))
    stride = width * channels
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                up_left = prev[i - channels] if i >= channels else 0
                row[i] = (row[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif kind != 0:
            raise ValueError(f'{path}: bad filter type {kind}')
        rows.append(row)
        prev = row
    return width, height, channels, rows


def write_png(path: str, width: int, height: int, channels: int, rows):
    """Write 8-bit rows (as returned by `read_png`) to `path`, unfiltered."""
    colour = {v: k for k, v in _CHANNELS.items()}[channels]

    def _chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack('>I', len(body)) + kind + body
                + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF))

    raw = b''.join(b'\x00' + bytes(row) for row in rows)
    with open(path, 'wb') as f:
        f.write(_PNG_SIG)
        f.write(_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, colour, 0, 0, 0)))
        f.write(_chunk(b'IDAT', zlib.compress(raw, 9)))
        f.write(_chunk(b'IEND', b''))


def downscale_png(src: str, dst: str, size: int):
    """Box-filter `src` down to `size` x `size` px (its sides must be a multiple of `size`)
    and write it to `dst`. Colour is alpha-weighted so transparent pixels don't darken edges."""
    width, height, channels, rows = read_png(src)
    if width % size or height % size:
        raise ValueError(f'{src}: {width}x{height} does not divide into {size}px')
    fx, fy = width // size, height // size
    has_alpha = channels in (2, 4)
    colour_n = channels - 1 if has_alpha else channels
    out = []
    for oy in range(size):
        row = bytearray(size * channels)
        block_rows = rows[oy * fy:(oy + 1) * fy]
        for ox in range(size):
            sums = [0] * channels
            alpha_sum = 0
            for src_row in block_rows:
                for x in range(ox * fx, (ox + 1) * fx):
                    base = x * channels
                    a = src_row[base + channels - 1] if has_alpha else 255
                    alpha_sum += a
                    for c in range(colour_n):
                        sums[c] += src_row[base + c] * a
            n = fx * fy
            base = ox * channels
            for c in range(colour_n):
                row[base + c] = sums[c] // alpha_sum if alpha_sum else 0
            if has_alpha:
                row[base + channels - 1] = alpha_sum // n
        out.append(row)
    write_png(dst, size, size, channels, out)

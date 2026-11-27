"""An independent QR Code Model 2 reader for sampled module matrices (ISO/IEC 18004), used only by the tests.

It was written separately from tools/l3mon/verify/qr.py, from the standard alone and without reading the encoder, so the two cannot share a mistake:
it reads both format-information copies (BCH-checked), the version information, the zigzag data, de-interleaves the blocks, checks every
Reed-Solomon syndrome is zero and parses the segments. decode(rows) takes rows of '0'/'1' strings ('1' = dark) and returns a dict.
"""

# ---------- GF(256), poly 0x11D ----------
EXP = [0] * 512
LOG = [0] * 256
v = 1
for i in range(255):
    EXP[i] = v
    LOG[v] = i
    v <<= 1
    if v & 0x100:
        v ^= 0x11D
for i in range(255, 512):
    EXP[i] = EXP[i - 255]

def gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]

def syndromes(cw, nec):
    out = []
    for i in range(nec):
        x = EXP[i]
        s = 0
        for c in cw:  # cw[0] is highest-degree coefficient
            s = gmul(s, x) ^ c
        out.append(s)
    return out

# ---------- tables ----------
# version -> level -> (ec per block, [(count, data codewords per block)])
BLOCKS = {
    1: {'L': (7, [(1, 19)]), 'M': (10, [(1, 16)]), 'Q': (13, [(1, 13)]), 'H': (17, [(1, 9)])},
    2: {'L': (10, [(1, 34)]), 'M': (16, [(1, 28)]), 'Q': (22, [(1, 22)]), 'H': (28, [(1, 16)])},
    3: {'L': (15, [(1, 55)]), 'M': (26, [(1, 44)]), 'Q': (18, [(2, 17)]), 'H': (22, [(2, 13)])},
    4: {'L': (20, [(1, 80)]), 'M': (18, [(2, 32)]), 'Q': (26, [(2, 24)]), 'H': (16, [(4, 9)])},
    5: {'L': (26, [(1, 108)]), 'M': (24, [(2, 43)]), 'Q': (18, [(2, 15), (2, 16)]), 'H': (22, [(2, 11), (2, 12)])},
    6: {'L': (18, [(2, 68)]), 'M': (16, [(4, 27)]), 'Q': (24, [(4, 19)]), 'H': (28, [(4, 15)])},
    7: {'L': (20, [(2, 78)]), 'M': (18, [(4, 31)]), 'Q': (18, [(2, 14), (4, 15)]), 'H': (26, [(4, 13), (1, 14)])},
    8: {'L': (24, [(2, 97)]), 'M': (22, [(2, 38), (2, 39)]), 'Q': (22, [(4, 18), (2, 19)]), 'H': (26, [(4, 14), (2, 15)])},
    9: {'L': (30, [(2, 116)]), 'M': (22, [(3, 36), (2, 37)]), 'Q': (20, [(4, 16), (4, 17)]), 'H': (24, [(4, 12), (4, 13)])},
    10: {'L': (18, [(2, 68), (2, 69)]), 'M': (26, [(4, 43), (1, 44)]), 'Q': (24, [(6, 19), (2, 20)]), 'H': (28, [(6, 15), (2, 16)])},
}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
         7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
ECL_FROM_BITS = {1: 'L', 0: 'M', 3: 'Q', 2: 'H'}

def bch_rem(val, nbits, gen, gdeg):
    for i in range(nbits - 1, gdeg - 1, -1):
        if (val >> i) & 1:
            val ^= gen << (i - gdeg)
    return val

MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
]

def decode(rows):
    res = {'errors': []}
    size = len(rows)
    if any(len(r) != size for r in rows) or (size - 17) % 4:
        raise ValueError('bad size')
    ver = (size - 17) // 4
    res['version'] = ver
    M = lambda x, y: rows[y][x] == '1'  # x = column, y = row

    # ---- format info, two copies ----
    f1 = 0
    for i in range(6):
        f1 |= M(8, i) << i
    f1 |= M(8, 7) << 6
    f1 |= M(8, 8) << 7
    f1 |= M(7, 8) << 8
    for i in range(9, 15):
        f1 |= M(14 - i, 8) << i
    f2 = 0
    for i in range(8):
        f2 |= M(size - 1 - i, 8) << i
    for i in range(8, 15):
        f2 |= M(8, size - 15 + i) << i
    u1, u2 = f1 ^ 0x5412, f2 ^ 0x5412
    v1 = bch_rem(u1, 15, 0x537, 10) == 0
    v2 = bch_rem(u2, 15, 0x537, 10) == 0
    res['format_raw'] = (f1, f2)
    fmt_ok = v1 and v2 and u1 == u2
    if not fmt_ok:
        res['errors'].append('format info: copy1 valid=%s copy2 valid=%s agree=%s' % (v1, v2, u1 == u2))
    u = u1 if v1 else u2
    data5 = u >> 10
    ecl = ECL_FROM_BITS[data5 >> 3]
    mask = data5 & 7
    res['format_ok'] = fmt_ok
    res['ecl'] = ecl
    res['mask'] = mask
    if not M(8, size - 8):
        res['errors'].append('dark module missing')

    # ---- version info ----
    vi_ok = None
    if ver >= 7:
        va = vb = 0
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            va |= M(a, b) << i   # top-right block
            vb |= M(b, a) << i   # bottom-left block
        oka = bch_rem(va, 18, 0x1F25, 12) == 0 and (va >> 12) == ver
        okb = bch_rem(vb, 18, 0x1F25, 12) == 0 and (vb >> 12) == ver
        vi_ok = oka and okb and va == vb
        if not vi_ok:
            res['errors'].append('version info bad: %x %x' % (va, vb))
    res['version_info_ok'] = vi_ok

    # ---- function pattern map ----
    func = [[False] * size for _ in range(size)]
    def fset(x, y):
        if 0 <= x < size and 0 <= y < size:
            func[y][x] = True
    for cx, cy in ((0, 0), (size - 7, 0), (0, size - 7)):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                fset(cx + dx, cy + dy)  # finder + separator
    for i in range(size):
        fset(6, i); fset(i, 6)  # timing
    al = ALIGN[ver]
    for ay in al:
        for ax in al:
            if (ax == 6 and ay == 6) or (ax == 6 and ay == al[-1]) or (ax == al[-1] and ay == 6):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    fset(ax + dx, ay + dy)
    for i in range(9):
        fset(8, i); fset(i, 8)
    for i in range(8):
        fset(size - 1 - i, 8); fset(8, size - 1 - i)
    fset(8, size - 8)
    if ver >= 7:
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            fset(a, b); fset(b, a)

    # ---- zigzag read ----
    mfn = MASKS[mask]
    bits = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not func[y][x]:
                    bits.append(int(M(x, y)) ^ int(mfn(x, y)))
        right -= 2
    ncw = len(bits) // 8
    cws = [int(''.join(map(str, bits[i * 8:i * 8 + 8])), 2) for i in range(ncw)]

    # ---- deinterleave ----
    nec, groups = BLOCKS[ver][ecl]
    dlens = [d for c, d in groups for _ in range(c)]
    total = sum(dlens) + nec * len(dlens)
    if total != ncw:
        res['errors'].append('codeword count mismatch: table %d vs matrix %d' % (total, ncw))
    nb = len(dlens)
    blocks = [[] for _ in range(nb)]
    k = 0
    for i in range(max(dlens)):
        for b in range(nb):
            if i < dlens[b]:
                blocks[b].append(cws[k]); k += 1
    for i in range(nec):
        for b in range(nb):
            blocks[b].append(cws[k]); k += 1
    synd = [syndromes(blk, nec) for blk in blocks]
    rs_ok = all(s == 0 for sb in synd for s in sb)
    res['rs_ok'] = rs_ok
    res['nonzero_syndrome_blocks'] = [i for i, sb in enumerate(synd) if any(sb)]
    if not rs_ok:
        res['errors'].append('RS syndromes non-zero in blocks %s' % res['nonzero_syndrome_blocks'])
    data = [c for b in range(nb) for c in blocks[b][:dlens[b]]]

    # ---- segment parse ----
    dbits = ''.join(format(c, '08b') for c in data)
    pos = 0
    def take(n):
        nonlocal pos
        s = dbits[pos:pos + n]
        pos += n
        return int(s, 2) if s else 0
    out = bytearray()
    modes = []
    while pos + 4 <= len(dbits):
        mode = take(4)
        if mode == 0:
            break
        if mode == 4:
            n = take(8 if ver <= 9 else 16)
            modes.append(('byte', n))
            for _ in range(n):
                out.append(take(8))
        elif mode == 1:
            n = take(10 if ver <= 9 else 12)
            modes.append(('numeric', n))
            s = ''
            while n >= 3:
                s += '%03d' % take(10); n -= 3
            if n == 2: s += '%02d' % take(7)
            elif n == 1: s += '%d' % take(4)
            out += s.encode()
        elif mode == 2:
            n = take(9 if ver <= 9 else 11)
            modes.append(('alnum', n))
            A = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:'
            s = ''
            while n >= 2:
                vv = take(11); s += A[vv // 45] + A[vv % 45]; n -= 2
            if n: s += A[take(6)]
            out += s.encode()
        elif mode == 7:
            modes.append(('eci', take(8)))
        else:
            modes.append(('unsupported mode %d' % mode, None))
            res['errors'].append('unsupported mode %d' % mode)
            break
    res['modes'] = modes
    try:
        res['text'] = out.decode('utf-8')
    except UnicodeDecodeError as e:
        res['text'] = None
        res['errors'].append('utf8: %s' % e)
    # padding check
    padpos = ((pos + 7) // 8)
    pads = data[padpos:]
    res['pad_ok'] = all(p == (0xEC if i % 2 == 0 else 0x11) for i, p in enumerate(pads))
    res['_codewords'] = cws
    res['_blocks'] = blocks
    return res

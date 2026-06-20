"""GNU ddrescue mapfile parsing and SVG rendering for the review UI.

Extracted from bin/image-serve.py (#18) as a cohesive, dependency-free unit so
the monolith shrinks and this logic becomes unit-testable in isolation. Pure
stdlib; no HTTP/server state.
"""
from __future__ import annotations

# status char -> (svg colour, human label)
MAP_STATUS = {
    '+': ('#22aa44', 'rescued'),
    '-': ('#334466', 'non-tried'),
    '/': ('#cc9900', 'non-trimmed'),
    '*': ('#dd6600', 'non-scraped'),
    '?': ('#cc2222', 'bad-sector'),
}
# lower = worse; used so a single bad block in a cell isn't masked by good data
MAP_PRIORITY = {'+': 4, '-': 3, '/': 2, '*': 1, '?': 0}


def parse_mapfile(path):
    """Parse a GNU ddrescue mapfile. Returns (meta, blocks).

    meta  — dict: current_pos, current_status, current_pass, start_time,
                  current_time, finished, command_line
    blocks — list of (pos_bytes: int, size_bytes: int, status_char: str)
    """
    meta = {k: None for k in ('current_pos', 'current_status', 'current_pass',
                               'start_time', 'current_time', 'command_line')}
    meta['finished'] = False
    blocks = []
    section = None  # 'header' | 'data'

    try:
        with open(path, 'r', errors='replace') as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith('#'):
                    c = line[1:].strip()
                    if c.startswith('Command line:'):
                        meta['command_line'] = c[len('Command line:'):].strip()
                    elif c.startswith('Start time:'):
                        meta['start_time'] = c[len('Start time:'):].strip()
                    elif c.startswith('Current time:'):
                        meta['current_time'] = c[len('Current time:'):].strip()
                    elif 'Finished' in c:
                        meta['finished'] = True
                    elif 'current_pos' in c:
                        section = 'header'
                    elif 'pos' in c and 'size' in c and 'status' in c:
                        section = 'data'
                    continue
                parts = line.split()
                if section == 'header' and len(parts) >= 2:
                    try:
                        meta['current_pos'] = int(parts[0], 16)
                        meta['current_status'] = parts[1]
                        if len(parts) >= 3:
                            meta['current_pass'] = int(parts[2])
                    except ValueError:
                        pass
                    section = 'data'
                elif section == 'data' and len(parts) >= 3:
                    try:
                        blocks.append((int(parts[0], 16), int(parts[1], 16), parts[2]))
                    except ValueError:
                        pass
    except OSError:
        pass

    return meta, blocks


def map_svg(blocks, cols=200, cell_w=5, cell_h=5):
    """Rasterize ddrescue blocks as a colored SVG grid.

    Returns (svg_html, stats_dict).  Uses worst-status-wins per cell so a
    single bad block in a region isn't hidden by surrounding good data.
    """
    if not blocks:
        return '<p class="count">No blocks in map file.</p>', {}

    total_size = max(pos + sz for pos, sz, _ in blocks)
    if total_size == 0:
        return '<p class="count">Map covers zero bytes.</p>', {}

    target = 5000
    bpc = max(512, (total_size + target - 1) // target)  # bytes per cell
    num_cells = (total_size + bpc - 1) // bpc
    rows = (num_cells + cols - 1) // cols
    num_pad = rows * cols  # padded so last row is complete

    # None = no block seen yet for this cell; filled in below
    cell_st = [None] * num_pad

    for pos, sz, st in blocks:
        c0 = pos // bpc
        c1 = min(num_pad - 1, (pos + sz - 1) // bpc)
        prio = MAP_PRIORITY.get(st, 5)
        for c in range(c0, c1 + 1):
            existing = cell_st[c]
            if existing is None or prio < MAP_PRIORITY.get(existing, 5):
                cell_st[c] = st

    pad = 2
    svg_w = cols * cell_w + 2 * pad
    svg_h = rows * cell_h + 2 * pad

    rects = []
    for idx in range(num_pad):
        st = cell_st[idx] or '-'   # uncovered cells → non-tried
        color = MAP_STATUS.get(st, ('#888', 'unknown'))[0]
        cx = pad + (idx % cols) * cell_w
        cy = pad + (idx // cols) * cell_h
        rects.append(f'<rect x="{cx}" y="{cy}" width="{cell_w}" height="{cell_h}" fill="{color}"/>')

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" '
        f'style="display:block;background:#111;border-radius:4px">'
        + ''.join(rects) + '</svg>'
    )

    byte_stats = {}
    for _, sz, st in blocks:
        byte_stats[st] = byte_stats.get(st, 0) + sz

    return svg, {'total_size': total_size, 'bpc': bpc, 'bytes': byte_stats}

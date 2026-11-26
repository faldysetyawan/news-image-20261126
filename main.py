#!/usr/bin/env python3
"""THE WORLD TODAY — Thursday 26 November 2026.

A deterministic, stdlib-only Python program that draws a broadsheet
front page summing up the world news of 26 November 2026 in one
1024x1024 PNG. Everything on the page is drawn by this program: its
own supersampled raster canvas, its own stroke typefaces, its own
PNG encoder. No network, no randomness, no external assets.

Run:  mkdir -p out && MAKEFUN_OUTPUT_DIR=out python3 main.py
Writes entry.png into $MAKEFUN_OUTPUT_DIR (default: out).
"""

import math
import os
import struct
import zlib

# ---------------------------------------------------------------------------
# Palette — warm newsprint, ink, one rationed red accent.
# ---------------------------------------------------------------------------

PAPER = (247, 243, 234)
INK = (26, 25, 22)
INK_SOFT = (52, 50, 45)
INK_FAINT = (122, 117, 107)
RULE_GREY = (168, 161, 148)
RED = (172, 28, 36)
NAVY = (30, 52, 84)
GREEN = (22, 106, 62)
SKY = (172, 184, 198)
WHITE = (255, 255, 255)

SS = 2  # supersampling factor for anti-aliasing


# ---------------------------------------------------------------------------
# Raster canvas. Page coordinates in, supersampled pixels inside.
# ---------------------------------------------------------------------------

class Raster:
    def __init__(self, w, h, bg):
        self.w = w * SS
        self.h = h * SS
        self.px = bytearray(bytes(bg) * (self.w * self.h))
        self._disc_cache = {}

    def _spans(self, r):
        """Cached (dy, half_width) rows for a filled disc of radius r px."""
        key = round(r, 2)
        got = self._disc_cache.get(key)
        if got is not None:
            return got
        spans = []
        ri = int(math.ceil(r))
        for dy in range(-ri, ri + 1):
            hw = int(math.sqrt(max(0.0, r * r - dy * dy)))
            spans.append((dy, hw))
        self._disc_cache[key] = spans
        return spans

    def _stamp(self, cx, cy, r, rgb):
        b = bytes(rgb)
        for dy, hw in self._spans(r):
            yy = cy + dy
            if 0 <= yy < self.h:
                x0 = max(0, cx - hw)
                x1 = min(self.w, cx + hw + 1)
                if x1 > x0:
                    i = (yy * self.w + x0) * 3
                    self.px[i:i + (x1 - x0) * 3] = b * (x1 - x0)

    def fill_rect(self, x, y, w, h, rgb):
        x0, y0 = int(round(x * SS)), int(round(y * SS))
        x1, y1 = int(round((x + w) * SS)), int(round((y + h) * SS))
        x0, x1 = max(0, x0), min(self.w, x1)
        if x1 <= x0:
            return
        row = bytes(rgb) * (x1 - x0)
        for yy in range(max(0, y0), min(self.h, y1)):
            i = (yy * self.w + x0) * 3
            self.px[i:i + len(row)] = row

    def fill_circle(self, cx, cy, r, rgb):
        self._stamp(int(round(cx * SS)), int(round(cy * SS)), r * SS, rgb)

    def line(self, x0, y0, x1, y1, width, rgb):
        ax, ay = x0 * SS, y0 * SS
        bx, by = x1 * SS, y1 * SS
        r = max(0.6, width * SS / 2.0)
        dist = math.hypot(bx - ax, by - ay)
        steps = max(1, int(dist / max(0.5, r * 0.45)))
        for i in range(steps + 1):
            t = i / steps
            self._stamp(int(round(ax + (bx - ax) * t)),
                        int(round(ay + (by - ay) * t)), r, rgb)

    def polyline(self, pts, width, rgb, close=False):
        seq = list(pts)
        if close and len(seq) > 2:
            seq.append(seq[0])
        for a, b in zip(seq, seq[1:]):
            self.line(a[0], a[1], b[0], b[1], width, rgb)

    def fill_polygon(self, pts, rgb):
        sp = [(x * SS, y * SS) for x, y in pts]
        ys = [p[1] for p in sp]
        y_lo = max(0, int(math.floor(min(ys))))
        y_hi = min(self.h - 1, int(math.ceil(max(ys))))
        b = bytes(rgb)
        n = len(sp)
        for yy in range(y_lo, y_hi + 1):
            xs = []
            for i in range(n):
                x0, y0 = sp[i]
                x1, y1 = sp[(i + 1) % n]
                if (y0 <= yy < y1) or (y1 <= yy < y0):
                    xs.append(x0 + (yy - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                x0 = max(0, int(round(xs[k])))
                x1 = min(self.w, int(round(xs[k + 1])))
                if x1 > x0:
                    i = (yy * self.w + x0) * 3
                    self.px[i:i + (x1 - x0) * 3] = b * (x1 - x0)

    def downscale(self):
        """Average SSxSS blocks; returns list of 1024-wide RGB rows."""
        ow, oh = self.w // SS, self.h // SS
        rows = []
        px = self.px
        stride = self.w * 3
        for oy in range(oh):
            row = bytearray(ow * 3)
            base = oy * SS * stride
            for ox in range(ow):
                i0 = base + ox * SS * 3
                for c in range(3):
                    s = 0
                    for dy in range(SS):
                        j = i0 + dy * stride + c
                        for dx in range(SS):
                            s += px[j + dx * 3]
                    row[ox * 3 + c] = s // (SS * SS)
            rows.append(bytes(row))
        return rows


# ---------------------------------------------------------------------------
# PNG encoder (filter 0 rows, zlib, CRC chunks) — byte-deterministic.
# ---------------------------------------------------------------------------

def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(path, w, h, rows):
    raw = b"".join(b"\x00" + r for r in rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) +
           _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------------------
# Stroke typeface. Glyphs live on a grid: y=0 baseline, y=14 cap height,
# y=10 x-height, descenders to y=-4. Each glyph string lists strokes
# separated by "|"; a stroke is space-separated "x,y" points, or "D x,y"
# for a dot. Advance width = widest x + 1.6 units (space = 5).
# ---------------------------------------------------------------------------

_FONT_SRC = {
    "A": "0,0 5,14 10,0|2.5,5 7.5,5",
    "B": "0,0 0,14|0,14 6,14 8.5,11 6,7.5 0,7.5|0,7.5 7,7.5 9,4 7,0 0,0",
    "C": "9,11 6,14 3,13 1,10 1,4 3,1 6,0 9,3",
    "D": "0,0 0,14|0,14 6,14 9,10 9,4 6,0 0,0",
    "E": "9,14 0,14 0,0 9,0|0,7 7,7",
    "F": "9,14 0,14 0,0|0,7 7,7",
    "G": "9,11 6,14 3,13 1,10 1,4 3,1 6,0 9,2 9,6 5,6",
    "H": "0,0 0,14|10,0 10,14|0,7 10,7",
    "I": "5,0 5,14|2,14 8,14|2,0 8,0",
    "J": "9,14 9,3 7,0 3,0 1,2",
    "K": "0,0 0,14|9,14 0,6|3,8.5 9,0",
    "L": "0,14 0,0 9,0",
    "M": "0,0 0,14 5,6 10,14 10,0",
    "N": "0,0 0,14 10,0 10,14",
    "O": "3,0 1,3 1,11 3,14 7,14 9,11 9,3 7,0 3,0",
    "P": "0,0 0,14|0,14 7,14 9,11 7,7.5 0,7.5",
    "Q": "3,0 1,3 1,11 3,14 7,14 9,11 9,3 7,0 3,0|6.5,2.5 10,-1",
    "R": "0,0 0,14|0,14 7,14 9,11 7,7.5 0,7.5|5,7.5 9,0",
    "S": "9,11.5 6,14 3,13.5 1.5,10.5 3,7.5 7,6 8.5,3.5 7,0.5 3,0 1,2.5",
    "T": "5,0 5,14|0,14 10,14",
    "U": "0,14 0,4 2,1 5,0 8,1 10,4 10,14",
    "V": "0,14 5,0 10,14",
    "W": "0,14 2.5,0 5,9 7.5,0 10,14",
    "X": "0,0 10,14|0,14 10,0",
    "Y": "0,14 5,7 10,14|5,7 5,0",
    "Z": "0,14 10,14 0,0 10,0",
    "a": "7,0 7,10|7,8 4,10 1,7 1,3 4,0 7,2",
    "b": "1,14 1,0|1,7 4,10 7,8 8,4 7,1 4,0 1,2",
    "c": "8,8 5,10 2,8 1,5 2,2 5,0 8,2",
    "d": "8,14 8,0|8,7 5,10 2,8 1,4 2,1 5,0 8,2",
    "e": "1,6 8,6 8,8 5,10 2,8 1,4 2,1 5,0 8,2",
    "f": "8,13 6,14 4,13 4,0|2,9 6,9",
    "g": "8,10 8,-2 6,-4 2,-4 1,-2|8,7 5,10 2,8 1,4 2,1 5,0 8,2",
    "h": "1,14 1,0|1,6 3,9 6,10 8,8 8,0",
    "i": "4,10 4,0|D 4,13",
    "j": "6,10 6,-2 4,-4 1,-4|D 6,13",
    "k": "1,14 1,0|8,10 2,5|4,7 8,0",
    "l": "4,14 4,0",
    "m": "1,10 1,0|1,7 3,10 5,9 5,0|5,7 7,10 9,9 9,0",
    "n": "1,10 1,0|1,7 4,10 7,9 8,7 8,0",
    "o": "3,0 1,3 1,7 3,10 6,10 8,7 8,3 6,0 3,0",
    "p": "1,10 1,-4|1,7 4,10 7,8 8,4 7,1 4,0 1,2",
    "q": "8,10 8,-4|8,7 5,10 2,8 1,4 2,1 5,0 8,2",
    "r": "1,10 1,0|1,7 3,10 6,10 8,8",
    "s": "8,8 5,10 2,9 1,6.5 3,4.5 6,3.5 7.5,1.5 5,0 2,0.5 1,2",
    "t": "4,13 4,2 6,0 8,0.5|2,9 7,9",
    "u": "1,10 1,3 3,0 6,0 8,3 8,10|8,10 8,0",
    "v": "0,10 4,0 8,10",
    "w": "0,10 2,0 4,7 6,0 8,10",
    "x": "1,0 7,10|1,10 7,0",
    "y": "1,10 4,4 7,10|4,4 3,-2 1,-4",
    "z": "1,10 8,10 1,0 8,0",
    "0": "3,0 1,3 1,11 3,14 6,14 8,11 8,3 6,0 3,0",
    "1": "3,11 6,14 6,0|3,0 9,0",
    "2": "2,11 4,14 7,13 8,10 2,0 9,0",
    "3": "2,12 5,14 7,12 5,8 7,6 8,3 5,0 2,2",
    "4": "7,0 7,14 1,4 9,4",
    "5": "8,14 2,14 2,8 6,8 8,5 7,1 4,0 2,2",
    "6": "8,12 5,14 2,10 1,5 2,1 5,0 8,2 8,6 5,8 2,6",
    "7": "1,14 9,14 5,0",
    "8": "4,7 2,10 3,14 6,14 8,11 6,7 8,4 7,0 3,0 2,4 4,7",
    "9": "2,2 5,0 8,4 9,9 8,13 5,14 2,12 2,8 5,6 8,8",
    ".": "D 3,0.5",
    ",": "3,1 2,-2",
    ":": "D 3,10|D 3,2",
    ";": "D 3,10|3,1 2,-2",
    "!": "3,14 3,4|D 3,0.5",
    "?": "2,11 4,14 7,13 7,10 4,7 4,4|D 4,0.5",
    "'": "3,14 2,10",
    '"': "2,14 1,10|5,14 4,10",
    "-": "1,5 7,5",
    "–": "1,5 9,5",
    "—": "0,5 12,5",
    "·": "D 3,7",
    "%": "1,14 8,0|2,11 1,12.5 2,14 4,13 4,11 2,10|6,4 5,5.5 6,7 8,6 8,4 6,3",
    "$": "4,-1 4,15|8,11.5 6,14 3,13.5 1.5,10.5 3,7.5 7,6 8.5,3.5 7,0.5 3,0 1,2.5",
    "°": "3,10.5 2,12 3,13.5 5,13 5.5,11.5 4,10.5 3,10.5",
    "(": "6,14 3,10 3,4 6,0",
    ")": "3,14 6,10 6,4 3,0",
    "&": "8,0 3,6 2,9 4,13 6,12 5,8 2,4 3,1 6,0 9,2",
    "/": "1,0 7,14",
    "+": "4,2 4,10|1,6 7,6",
    "=": "1,8 7,8|1,4 7,4",
}


def _parse_font():
    font = {" ": (6.5, [])}
    for ch, src in _FONT_SRC.items():
        strokes = []
        max_x = 0.0
        for part in src.split("|"):
            toks = part.split()
            if toks[0] == "D":
                x, y = (float(v) for v in toks[1].split(","))
                strokes.append(("D", x, y))
                max_x = max(max_x, x)
            else:
                pts = []
                for tok in toks:
                    x, y = (float(v) for v in tok.split(","))
                    pts.append((x, y))
                    max_x = max(max_x, x)
                strokes.append(pts)
        font[ch] = (max_x + 1.6, strokes)
    return font


FONT = _parse_font()


def _glyph(ch):
    return FONT.get(ch, FONT["?"])


def text_width(text, size, tracking=0.0):
    scale = size / 14.0
    w = 0.0
    for ch in text:
        w += _glyph(ch)[0] * scale + tracking
    return w - (tracking if text else 0.0)


def draw_text(cv, x, y, text, size, rgb, weight=2.0, tracking=0.0,
              align="left"):
    """Draw text with baseline at y. Returns the width drawn."""
    scale = size / 14.0
    total = text_width(text, size, tracking)
    if align == "center":
        x -= total / 2.0
    elif align == "right":
        x -= total
    cx = x
    for ch in text:
        adv, strokes = _glyph(ch)
        for stroke in strokes:
            if stroke[0] == "D":
                cv.fill_circle(cx + stroke[1] * scale,
                               y - stroke[2] * scale,
                               max(0.7, weight * 0.62), rgb)
            else:
                pts = [(cx + px * scale, y - py * scale) for px, py in stroke]
                cv.polyline(pts, weight, rgb)
        cx += adv * scale + tracking
    return total


def wrap_lines(text, size, max_w, tracking=0.0):
    """Greedy word wrap; returns list of (line, width)."""
    words = text.split()
    lines = []
    cur = ""
    space_w = text_width(" ", size, tracking)
    for word in words:
        trial = word if not cur else cur + " " + word
        if cur and text_width(trial, size, tracking) > max_w:
            lines.append((cur, text_width(cur, size, tracking)))
            cur = word
        else:
            cur = trial
    if cur:
        lines.append((cur, text_width(cur, size, tracking)))
    return lines


def draw_paragraph(cv, x, y, text, size, leading, max_w, rgb, weight=1.9,
                   justify=False, max_lines=None, tracking=0.0):
    """Draw wrapped text, first baseline at y. Returns the baseline
    just past the block (last baseline + leading)."""
    lines = wrap_lines(text, size, max_w, tracking)
    if max_lines is not None:
        lines = lines[:max_lines]
    space_w = text_width(" ", size, tracking)
    for i, (line, lw) in enumerate(lines):
        last = i == len(lines) - 1
        words = line.split()
        if justify and not last and len(words) > 1:
            gaps = len(words) - 1
            slack = max_w - lw
            if 0 < slack / gaps <= 7.0:
                cx = x
                extra = slack / gaps
                for word in words:
                    draw_text(cv, cx, y, word, size, rgb, weight, tracking)
                    cx += text_width(word, size, tracking) + space_w + extra
            else:
                draw_text(cv, x, y, line, size, rgb, weight, tracking)
        else:
            draw_text(cv, x, y, line, size, rgb, weight, tracking)
        y += leading
    return y


def autofit_size(text, size, min_size, max_w, max_lines, tracking=0.0):
    """Largest size <= size (stepping down) whose wrap fits max_lines."""
    s = size
    while s > min_size:
        if len(wrap_lines(text, s, max_w, tracking)) <= max_lines:
            return s
        s -= 1.0
    return min_size


# ---------------------------------------------------------------------------
# Furniture helpers.
# ---------------------------------------------------------------------------

MX = 40
CW = 1024 - 2 * MX
RIGHT = MX + CW


def rule(cv, x, y, w, thick=1.1, rgb=INK):
    cv.fill_rect(x, y, w, thick, rgb)


def kicker(cv, x, y, text, size=14.0):
    draw_text(cv, x, y, text, size, RED, weight=2.4, tracking=1.8)


def draw_headline(cv, x, y_top, text, max_size, min_size, max_w, max_lines,
                  weight=5.0, leading=1.21):
    """Auto-fit headline; cap top at y_top. Returns last baseline."""
    size = autofit_size(text, max_size, min_size, max_w, max_lines)
    lines = wrap_lines(text, size, max_w)[:max_lines]
    y = y_top + size
    for line, _ in lines:
        draw_text(cv, x, y, line, size, INK, weight=weight)
        y += size * leading
    return y - size * leading


# ---------------------------------------------------------------------------
# Lead visual: a field of crosses, one per 10,000 recorded dead.
# ---------------------------------------------------------------------------

def visual_crosses(cv, x, y, w, h):
    cv.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                1.8, INK, close=True)
    cols, rows = 7, 7
    gx0, gy0 = x + 16, y + 14
    gw, gh = w - 32, h - 48
    cell_w, cell_h = gw / cols, gh / rows
    for r in range(rows):
        for c in range(cols):
            cx = gx0 + cell_w * (c + 0.5)
            cy = gy0 + cell_h * (r + 0.5)
            col = RED if (r == 3 and c == 3) else INK_SOFT
            cv.line(cx, cy - 6.5, cx, cy + 6.5, 2.2, col)
            cv.line(cx - 4, cy - 2.5, cx + 4, cy - 2.5, 2.2, col)
    draw_text(cv, x + w / 2, y + h - 10,
              "EACH CROSS = 10,000 DEAD · ROSSTAT RECORDS",
              10.5, INK_FAINT, weight=1.8, tracking=0.8, align="center")


# ---------------------------------------------------------------------------
# Secondary chart: signed bars around an axis (navy up, red down).
# ---------------------------------------------------------------------------

def visual_bars(cv, x, y, w, h, points, labels, big):
    axis = y + h * 0.52
    top_zone = axis - (y + 20)
    bot_zone = (y + h - 15) - axis
    rule(cv, x, axis, w, 1.2, INK_FAINT)
    top = max(abs(v) for v in points) or 1.0
    slot = w / len(points)
    bw = slot * 0.52
    for i, v in enumerate(points):
        cx = x + slot * (i + 0.5)
        if v >= 0:
            bh = (v / top) * top_zone * 0.92
            cv.fill_rect(cx - bw / 2, axis - bh, bw, bh, NAVY)
        else:
            bh = (-v / top) * bot_zone * 0.92
            cv.fill_rect(cx - bw / 2, axis, bw, bh, RED)
        draw_text(cv, cx, y + h - 3, labels[i], 9.0, INK_FAINT,
                  weight=1.6, tracking=0.6, align="center")
    draw_text(cv, x, y + 15, big, 17.0, INK, weight=2.8)


# ---------------------------------------------------------------------------
# Page assembly. Fixed anchors: secondary row ends at y=940, markets
# strip at y=950; every body is line-capped against its anchor.
# ---------------------------------------------------------------------------

def render(content, path):
    cv = Raster(1024, 1024, PAPER)
    c = content

    # Folio strip.
    rule(cv, MX, 26, CW, 2.2)
    draw_text(cv, MX, 44, c["folio_left"], 12.5, INK_SOFT, 2.1, tracking=1.6)
    draw_text(cv, 512, 44, c["edition"], 12.5, INK_SOFT, 2.1,
              tracking=1.6, align="center")
    draw_text(cv, RIGHT, 44, c["folio_right"], 12.5, INK_SOFT, 2.1,
              tracking=1.6, align="right")
    rule(cv, MX, 54, CW, 1.1)

    # Nameplate + double rule.
    draw_text(cv, 512, 150, "THE WORLD TODAY", 80, INK, weight=8.0,
              tracking=1.0, align="center")
    rule(cv, MX, 162, CW, 3.0)
    rule(cv, MX, 168, CW, 1.1)

    # Dateline row: home city, the date in ink, weather.
    draw_text(cv, MX, 190, c["dateline_left"], 10.5, INK_FAINT, 1.8,
              tracking=0.9)
    draw_text(cv, 512, 190, c["date_line"], 15, INK, 2.6, tracking=1.4,
              align="center")
    draw_text(cv, RIGHT, 190, c["weather"], 10.5, INK_FAINT, 1.8,
              tracking=0.9, align="right")
    rule(cv, MX, 202, CW, 1.1)

    # ---- Lead story -------------------------------------------------------
    lead = c["lead"]
    kicker(cv, MX, 226, lead["kicker"])
    hy = draw_headline(cv, MX, 226 + 16, lead["headline"], 57, 34,
                       CW, 2, weight=4.6)
    dy = draw_paragraph(cv, MX, hy + 30, lead["deck"], 20.5, 27, CW,
                        INK_SOFT, weight=2.2, max_lines=2)
    vy = dy - 27 + 16  # dy is one leading past the last deck baseline
    vw_, vh_ = 444, 204
    visual_crosses(cv, MX, vy, vw_, vh_)
    cap_y = draw_paragraph(cv, MX, vy + vh_ + 22, lead["caption"], 13.0,
                           16, vw_, INK_SOFT, weight=1.75, max_lines=2)
    credit_y = cap_y - 16 + 17
    draw_text(cv, MX, credit_y, lead["credit"], 10.5, INK_FAINT, 1.8,
              tracking=1.0)

    # Right zone: big stat (numeral, label+sub beside it), byline, body.
    tx = MX + vw_ + 24
    tw = RIGHT - tx
    ty = vy
    if lead.get("stat"):
        s = lead["stat"]
        draw_text(cv, tx, ty + 58, s["value"], 58, INK, weight=6.0)
        vw_stat = text_width(s["value"], 58)
        lx = tx + vw_stat + 16
        draw_text(cv, lx, ty + 24, s["label"], 13.5, RED, 2.4, tracking=1.6)
        sub_end = draw_paragraph(cv, lx, ty + 44, s["sub"], 13.5, 17.5,
                                 RIGHT - lx, INK_FAINT, weight=1.75,
                                 max_lines=3)
        ty = max(ty + 70, sub_end - 17.5 + 6)
    byline_y = ty + 24
    draw_text(cv, tx, byline_y, lead["byline"], 11, INK_FAINT, 1.8,
              tracking=1.2)
    body_y = byline_y + 24
    col_w = (tw - 22) / 2.0
    max_lines = max(2, int((vy + 250 - body_y) / 18.5) + 1)
    words = lead["body"].split()
    half = (len(words) + 1) // 2
    col1 = " ".join(words[:half])
    col2 = " ".join(words[half:])
    end1 = draw_paragraph(cv, tx, body_y, col1, 13.5, 18.5, col_w,
                          INK_SOFT, weight=1.75, justify=True,
                          max_lines=max_lines)
    end2 = draw_paragraph(cv, tx + col_w + 22, body_y, col2, 13.5, 18.5,
                          col_w, INK_SOFT, weight=1.75, justify=True,
                          max_lines=max_lines)
    body_end = max(end1, end2) - 18.5
    lead_bottom = max(credit_y + 8, body_end + 10)
    rule(cv, MX, lead_bottom + 12, CW, 3.0)

    # ---- Secondary row -----------------------------------------------------
    row_kicker_y = lead_bottom + 12 + 34
    col_x = [MX, 364, 688]
    col_w2 = 296
    cv.fill_rect(350, lead_bottom + 34, 1.1, 940 - (lead_bottom + 34),
                 RULE_GREY)
    cv.fill_rect(674, lead_bottom + 34, 1.1, 940 - (lead_bottom + 34),
                 RULE_GREY)
    for idx, st in enumerate(c["stories"]):
        x = col_x[idx]
        kicker(cv, x, row_kicker_y, st["kicker"], size=12.5)
        hy2 = draw_headline(cv, x, row_kicker_y + 14, st["headline"],
                            26.5, 19, col_w2, 2 if st.get("chart") else 3,
                            weight=2.5, leading=1.16)
        if st.get("chart"):
            ch = st["chart"]
            visual_bars(cv, x, hy2 + 12, col_w2, 60, ch["points"],
                        ch["labels"], ch["value"])
            body_first = hy2 + 12 + 60 + 18
        else:
            body_first = hy2 + 22
        cap = max(1, int((936 - body_first) / 18.5) + 1)
        draw_paragraph(cv, x, body_first, st["body"], 13.5, 18.5, col_w2,
                       INK_SOFT, weight=1.75, justify=True, max_lines=cap)

    # IN BRIEF column.
    bx = col_x[2]
    draw_text(cv, bx, row_kicker_y, "IN BRIEF", 13.5, INK, 2.6, tracking=2.2)
    rule(cv, bx, row_kicker_y + 8, col_w2, 1.6)
    by = row_kicker_y + 32
    for i, (label, text) in enumerate(c["briefs"]):
        if by > 906:
            break
        draw_text(cv, bx, by, label, 12, RED, 2.2, tracking=1.4)
        tend = draw_paragraph(cv, bx, by + 17, text, 12.5, 15.5, col_w2,
                              INK_SOFT, weight=1.8, max_lines=3)
        by = tend - 15.5 + 14
        if i < len(c["briefs"]) - 1:
            rule(cv, bx, by - 6, col_w2, 0.8, RULE_GREY)
            by += 8

    # ---- Markets strip + footer -------------------------------------------
    rule(cv, MX, 950, CW, 2.6)
    draw_text(cv, MX, 972, "MARKETS", 12.5, INK, 2.6, tracking=1.6)
    slot = (CW - 104) / max(1, len(c["markets"]))
    for i, (name, value, change) in enumerate(c["markets"]):
        x = MX + 104 + i * slot
        draw_text(cv, x, 968, name, 11, INK_FAINT, 1.9, tracking=0.8)
        draw_text(cv, x, 990, value, 16.5, INK, 2.5)
        tx2 = x + text_width(value, 16.5) + 9
        if change > 0:
            cv.fill_polygon([(tx2, 990), (tx2 + 8, 990), (tx2 + 4, 982)],
                            GREEN)
            draw_text(cv, tx2 + 12, 990, "+%.1f%%" % change, 12, GREEN, 2.1)
        elif change < 0:
            cv.fill_polygon([(tx2, 982), (tx2 + 8, 982), (tx2 + 4, 990)],
                            RED)
            draw_text(cv, tx2 + 12, 990, "%.1f%%" % change, 12, RED, 2.1)
        else:
            draw_text(cv, tx2, 990, "—", 12, INK_FAINT, 2.1)
    rule(cv, MX, 1002, CW, 1.1)
    draw_text(cv, 512, 1016, c["footer"], 10, INK_FAINT, 1.6, tracking=0.8,
              align="center")

    rows = cv.downscale()
    encode_png(path, 1024, 1024, rows)


# ---------------------------------------------------------------------------
# The day's content: Thursday 26 November 2026. Every story below was
# reported on 25-26 November 2026 and cross-checked against at least
# two sources (BBC Russian / Le Monde / Reuters / ESA releases and the
# day's market wires). Copy is written to the design budgets so no
# block is cut mid-sentence.
# ---------------------------------------------------------------------------

CONTENT = {
    "folio_left": "VOL. IV · NO. 330",
    "edition": "WORLD EDITION",
    "folio_right": "LATE CITY FINAL",
    "dateline_left": "LONDON",
    "date_line": "THURSDAY 26 NOVEMBER 2026",
    "weather": "LONDON 5° OVERCAST",
    "lead": {
        "kicker": "LONDON · UKRAINE WAR",
        "headline": "RUSSIA'S OWN FIGURES: 490,000 DEAD IN UKRAINE",
        "deck": "Leaked Russian death records analysed by the BBC put "
                "the toll at 490,334 soldiers since 2022.",
        "byline": "BY STAFF CORRESPONDENT",
        "body": "LONDON — BBC Russian analysed 11 million Rosstat "
                "death records, hacked by Cyber Partisans and Silent "
                "Crow. They show at least 490,334 dead since "
                "February 2022. Among Russians aged 18 to 24 who died "
                "last year, 65% were killed in the war. The Kremlin has "
                "not commented.",
        "caption": "Russia's dead in its own leaked records.",
        "credit": "Staff illustration",
        "stat": {
            "value": "490,334",
            "label": "SOLDIERS DEAD",
            "sub": "per leaked Rosstat records",
        },
    },
    "stories": [
        {
            "kicker": "RAS TANURA · GULF",
            "headline": "HOUTHIS HIT SAUDI REFINERY",
            "body": "Satellite images show thick smoke over Ras Tanura, "
                    "Saudi Arabia's largest refinery. Riyadh says four "
                    "drones were intercepted.",
        },
        {
            "kicker": "NEW YORK · MARKETS",
            "headline": "OIL JUMPS, STOCKS SLIP",
            "body": "Brent rose 3% after the strike. US markets shut "
                    "for Thanksgiving.",
            "chart": {
                "points": [2.97, 0.33, -0.25, -0.62, -2.78],
                "labels": ["BRENT", "DOW", "S&P", "NAS", "NIKK"],
                "value": "+3.0%",
            },
        },
    ],
    "briefs": [
        ("SPACE", "Asteroid crater shown by Hera"),
        ("PYONGYANG", "Swiss office ordered shut"),
        ("BOGOTA", "20 hostages freed by police"),
        ("GENEVA", "Gukesh defends chess crown"),
    ],
    "markets": [
        ("DOW", "50,183", 0.33),
        ("S&P 500", "7,183", -0.25),
        ("NASDAQ", "23,586", -0.62),
        ("BRENT", "$94.12", 2.97),
    ],
    "footer": "THE WORLD TODAY — drawn entirely by a program; no "
              "photographs. Thursday 26 November 2026.",
}


def main():
    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR", "out")
    os.makedirs(outdir, exist_ok=True)
    render(CONTENT, os.path.join(outdir, "entry.png"))


if __name__ == "__main__":
    main()

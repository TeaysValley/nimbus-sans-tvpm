#!/usr/bin/env python3
"""Build Nimbus Sans TVPM, the open-source stand-in for Neue Haas Grotesk Display in TVPM's
PDFs and decks, from URW's Nimbus Sans (urw-base35-fonts, AGPL 3 with the font exception).

    python3 -I tools/fonts/build-nimbus.py                  # fetch, build, emit, check
    python3 -I tools/fonts/build-nimbus.py --source DIR     # use an existing clone
    python3 -I tools/fonts/build-nimbus.py --check-only     # numbers for the fonts already built

What it does, per docs/export-fonts-brief.md section A:
  1. fetches ArtifexSoftware/urw-base35-fonts at the pinned commit (needs git and network)
  2. Bold: pushes every outline out 29 units sideways and 12 up and down (2,048 per em) so the
     stems match NHG Display 75 Bold (178 vs 177 per 1,000) and the horizontals do too (128 vs 131);
     cap height is held; the Regular's weight already matches and is not touched
  3. both weights: side bearings pulled in to NHG Display's fit (39 and 20 units per side, half on
     digits), word space set to NHG's, URW's GSUB and GPOS copied in from the .otf (liga, frac,
     numr, dnom, sups, ordn, kern) and scaled, the Bold's kerning scaled by 1.15, a legacy kern
     table written for PowerPoint, plus three features URW does not ship: pnum (proportional
     figures), tnum (the default, named so a stylesheet can ask for it) and case (dashes, bullets
     and guillemets raised for all-caps settings)
  4. emits assets/fonts/NimbusSansTVPMRegular.js and NimbusSansTVPMBold.js in the loader format
     the generators read, and assets/fonts/LICENSE-NimbusSans.txt
  5. --check prints the numbers the brief asks for, per weight

Needs: fontTools, skia-pathops (pip install fonttools skia-pathops). Nothing else.
"""
import argparse, base64, datetime, os, shutil, subprocess, sys, tempfile
from pathlib import Path

import pathops
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._k_e_r_n import KernTable_format_0
from fontTools.ttLib.tables import otTables
from fontTools.otlLib.builder import buildSingleSubstSubtable
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

REPO = "https://github.com/ArtifexSoftware/urw-base35-fonts.git"
COMMIT = "3c0ba3b5687632dfc66526544a4e811fe0ec0cd9"   # 2023-05-03, the last release of the set
FAMILY = "Nimbus Sans TVPM"
PUBLIC_REPO = "https://github.com/TeaysValley/nimbus-sans-tvpm"   # this script, the built .ttf files and the licence, where anyone can reach them
ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "assets" / "fonts"
BUILD = ROOT / "tools" / "fonts" / "build"

# the measured decisions (see the brief); units are the TTF's 2,048 per em
BOLD = dict(dx=29, dy=12, letters=39, digits=20, space=328, kern_scale=1.15, weight_class=700)
REGULAR = dict(letters=20, digits=10, space=338, kern_scale=1.0, weight_class=400)
FLOOR = 5          # a side bearing is never pulled under this, and one already under it is left alone
CASE_GLYPHS = ["hyphen", "endash", "emdash", "bullet", "periodcentered", "guillemotleft", "guillemotright",
               "guilsinglleft", "guilsinglright"]
DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


# ---------- helpers ----------
def pair_records(gpos):
    for lk in gpos.table.LookupList.Lookup:
        if lk.LookupType != 2:
            continue
        for st in lk.SubTable:
            if hasattr(st, "PairSet"):
                for ps in st.PairSet:
                    yield from ps.PairValueRecord
            else:
                for r1 in st.Class1Record:
                    yield from r1.Class2Record


def scale_pairs(gpos, k):
    for r in pair_records(gpos):
        if r.Value1 and hasattr(r.Value1, "XAdvance"):
            r.Value1.XAdvance = int(round(r.Value1.XAdvance * k))


def collect_pairs(otf):
    """Every pair with an XAdvance, flattened from the .otf's GPOS (class pairs expanded)."""
    pairs = {}
    for lk in otf["GPOS"].table.LookupList.Lookup:
        if lk.LookupType != 2:
            continue
        for st in lk.SubTable:
            if hasattr(st, "PairSet"):
                for f, ps in zip(st.Coverage.glyphs, st.PairSet):
                    for r in ps.PairValueRecord:
                        v = getattr(r.Value1, "XAdvance", 0) if r.Value1 else 0
                        if v:
                            pairs[(f, r.SecondGlyph)] = v
            else:
                cov = set(st.Coverage.glyphs)
                inv1, inv2 = {}, {}
                for g, c in st.ClassDef1.classDefs.items():
                    inv1.setdefault(c, []).append(g)
                for g, c in st.ClassDef2.classDefs.items():
                    inv2.setdefault(c, []).append(g)
                inv1.setdefault(0, [g for g in cov if g not in st.ClassDef1.classDefs])
                for i, r1 in enumerate(st.Class1Record):
                    for j, r2 in enumerate(r1.Class2Record):
                        v = getattr(r2.Value1, "XAdvance", 0) if r2.Value1 else 0
                        if not v:
                            continue
                        for f in inv1.get(i, []):
                            if f in cov:
                                for s in inv2.get(j, []):
                                    pairs.setdefault((f, s), v)
    return pairs


def embolden(font, dx, dy):
    """Offset every outline by dx sideways and dy up/down, hold the left side bearing and cap height."""
    glyf, hmtx = font["glyf"], font["hmtx"]
    glyph_set = font.getGlyphSet()
    cap = font["OS/2"].sCapHeight
    y_scale = cap / (cap + 2 * dy)
    stretch = dx / dy
    new = {}
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours == 0 and not g.isComposite():
            continue
        path = pathops.Path()
        glyph_set[name].draw(path.getPen())
        if not list(path):
            continue
        s = pathops.Path(path).transform(1, 0, 0, stretch, 0, 0)
        s.stroke(2 * dx, pathops.LineCap.ROUND_CAP, pathops.LineJoin.ROUND_JOIN, 4.0)
        s.convertConicsToQuads()
        s = s.transform(1, 0, 0, 1 / stretch, 0, 0)
        merged = pathops.op(path, s, pathops.PathOp.UNION, fix_winding=True, keep_starting_points=False)
        merged.simplify(fix_winding=True)
        pen = TTGlyphPen(None)
        merged.draw(Cu2QuPen(TransformPen(pen, (1, 0, 0, y_scale, dx, dy * y_scale)), max_err=1.0))
        new[name] = pen.glyph()
    for name, g in new.items():
        glyf[name] = g
        adv, _ = hmtx[name]
        g.recalcBounds(glyf)
        hmtx[name] = (int(round(adv + 2 * dx)), g.xMin)


def refit(font, letters, digits, space):
    glyf, hmtx = font["glyf"], font["hmtx"]
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    digit_names = {cmap[c] for c in range(ord("0"), ord("9") + 1) if c in cmap}
    space_name = cmap.get(32)

    def pull(sb, amount):
        if sb < FLOOR:
            return sb, 0
        n = max(FLOOR, sb - amount)
        return n, sb - n

    for name in font.getGlyphOrder():
        g = glyf[name]
        adv, lsb = hmtx[name]
        if name == space_name:
            hmtx[name] = (space, 0)
            continue
        if g.numberOfContours == 0 or adv == 0:
            continue
        if g.isComposite():
            g.recalcBounds(glyf)
        amount = digits if name in digit_names else letters
        rsb = adv - g.xMax
        new_lsb, dl = pull(lsb, amount)
        new_rsb, dr = pull(rsb, amount)
        if dl:
            pen = TTGlyphPen(glyph_set)
            glyph_set[name].draw(TransformPen(pen, (1, 0, 0, 1, -dl, 0)))
            g = pen.glyph(); g.recalcBounds(glyf); glyf[name] = g
        hmtx[name] = (int(round(adv - dl - dr)), g.xMin)


def port_layout(font, otf, kern_scale):
    k = font["head"].unitsPerEm / otf["head"].unitsPerEm
    font["GSUB"] = otf["GSUB"]
    font["GPOS"] = otf["GPOS"]
    scale_pairs(font["GPOS"], k * kern_scale)
    names = set(font.getGlyphOrder())
    kern = newTable("kern"); kern.version = 0
    st = KernTable_format_0(); st.version = 0; st.coverage = 1; st.format = 0
    st.kernTable = {p: int(round(v * k * kern_scale)) for p, v in collect_pairs(otf).items()
                    if p[0] in names and p[1] in names}
    kern.kernTables = [st]
    font["kern"] = kern


def add_feature(font, tag, mapping):
    """Append a single-substitution feature to GSUB for every script and language."""
    gsub = font["GSUB"].table
    lookup = otTables.Lookup()
    lookup.LookupType = 1; lookup.LookupFlag = 0
    lookup.SubTable = [buildSingleSubstSubtable(mapping)]
    lookup.SubTableCount = 1
    gsub.LookupList.Lookup.append(lookup)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    rec = otTables.FeatureRecord()
    rec.FeatureTag = tag
    rec.Feature = otTables.Feature()
    rec.Feature.FeatureParams = None
    rec.Feature.LookupListIndex = [len(gsub.LookupList.Lookup) - 1]
    rec.Feature.LookupCount = 1
    gsub.FeatureList.FeatureRecord.append(rec)
    gsub.FeatureList.FeatureCount = len(gsub.FeatureList.FeatureRecord)
    idx = gsub.FeatureList.FeatureCount - 1
    for sr in gsub.ScriptList.ScriptRecord:
        for ls in [sr.Script.DefaultLangSys] + [l.LangSys for l in sr.Script.LangSysRecord]:
            if ls is not None:
                ls.FeatureIndex.append(idx)
                ls.FeatureCount = len(ls.FeatureIndex)


def sort_features(font):
    """The spec wants FeatureRecords sorted by tag; remap every LangSys after sorting."""
    gsub = font["GSUB"].table
    recs = gsub.FeatureList.FeatureRecord
    order = sorted(range(len(recs)), key=lambda i: (recs[i].FeatureTag, i))
    remap = {old: new for new, old in enumerate(order)}
    gsub.FeatureList.FeatureRecord = [recs[i] for i in order]
    for sr in gsub.ScriptList.ScriptRecord:
        for ls in [sr.Script.DefaultLangSys] + [l.LangSys for l in sr.Script.LangSysRecord]:
            if ls is not None:
                ls.FeatureIndex = sorted(remap[i] for i in ls.FeatureIndex)


def add_tvpm_features(font, letters_sb):
    """pnum: digit copies with letter-like side bearings. tnum: the default, named. case: raised marks."""
    glyf, hmtx = font["glyf"], font["hmtx"]
    glyph_set = font.getGlyphSet()
    order = list(font.getGlyphOrder())
    cap, xh = font["OS/2"].sCapHeight, font["OS/2"].sxHeight

    def copy_glyph(src, dst, dx=0, dy=0, new_adv=None):
        pen = TTGlyphPen(glyph_set)
        glyph_set[src].draw(TransformPen(pen, (1, 0, 0, 1, dx, dy)))
        g = pen.glyph(); g.recalcBounds(glyf)
        glyf[dst] = g
        adv, _ = hmtx[src]
        hmtx[dst] = (new_adv if new_adv is not None else adv, g.xMin)
        order.append(dst)

    pnum = {}
    for d in DIGITS:
        if d not in glyf or glyf[d].numberOfContours == 0:
            continue
        g = glyf[d]; adv, lsb = hmtx[d]
        width = g.xMax - g.xMin
        sb = letters_sb
        copy_glyph(d, d + ".pnum", dx=sb - g.xMin, new_adv=width + 2 * sb)
        pnum[d] = d + ".pnum"
    case = {}
    for name in CASE_GLYPHS:
        if name not in glyf or glyf[name].numberOfContours == 0:
            continue
        bp = BoundsPen(glyph_set); glyph_set[name].draw(bp)
        y0, y1 = bp.bounds[1], bp.bounds[3]
        centre = (y0 + y1) / 2
        dy = int(round(cap / 2 - centre)) if centre < cap / 2 else 0
        if dy:
            copy_glyph(name, name + ".case", dy=dy)
            case[name] = name + ".case"
    font.setGlyphOrder(order)
    glyf.glyphOrder = order
    if "post" in font and getattr(font["post"], "formatType", 3.0) == 2.0:
        font["post"].extraNames = []  # rebuilt from the glyph order on compile
        font["post"].mapping = {}
    add_feature(font, "pnum", pnum)
    add_feature(font, "tnum", {d: d for d in pnum})   # identity, so a request for tnum is honored as the default
    if case:
        add_feature(font, "case", case)
    sort_features(font)
    return len(pnum), len(case)


def rename(font, style, weight_class):
    for rec in font["name"].names:
        if rec.nameID in (1, 16):
            rec.string = FAMILY
        elif rec.nameID == 2:
            rec.string = style
        elif rec.nameID == 4:
            rec.string = f"{FAMILY} {style}"
        elif rec.nameID == 6:
            rec.string = FAMILY.replace(" ", "") + "-" + style
    font["OS/2"].usWeightClass = weight_class
    font["OS/2"].fsType = 0   # installable embedding (URW's licence permits it): no restriction for PowerPoint to trip on


# ---------- build ----------
def build(src_dir: Path):
    fonts = src_dir / "fonts"
    BUILD.mkdir(parents=True, exist_ok=True)
    out = {}
    # Bold
    f = TTFont(fonts / "NimbusSans-Bold.ttf")
    embolden(f, BOLD["dx"], BOLD["dy"])
    refit(f, BOLD["letters"], BOLD["digits"], BOLD["space"])
    port_layout(f, TTFont(fonts / "NimbusSans-Bold.otf"), BOLD["kern_scale"])
    add_tvpm_features(f, letters_sb=43)
    rename(f, "Bold", BOLD["weight_class"])
    out["Bold"] = BUILD / "NimbusSansTVPM-Bold.ttf"; f.save(out["Bold"])
    # Regular
    f = TTFont(fonts / "NimbusSans-Regular.ttf")
    refit(f, REGULAR["letters"], REGULAR["digits"], REGULAR["space"])
    port_layout(f, TTFont(fonts / "NimbusSans-Regular.otf"), REGULAR["kern_scale"])
    add_tvpm_features(f, letters_sb=63)
    rename(f, "Regular", REGULAR["weight_class"])
    out["Regular"] = BUILD / "NimbusSansTVPM-Regular.ttf"; f.save(out["Regular"])
    return out


def emit(built: dict, src_dir: Path):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for style, path in built.items():
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode()
        name = f"NimbusSansTVPM{style}"
        js = (f"/**\n * {name}\n * Generated from: {path.name} (build-nimbus.py at {PUBLIC_REPO}, URW Nimbus Sans at {COMMIT[:12]})\n"
              f" * File size: {len(data)//1024} KB (original) → ~{len(b64)//1024} KB (base64)\n"
              f" * Font family: {FAMILY}\n * Font style: {'bold' if style == 'Bold' else 'normal'}\n *\n"
              f" * This file is loaded by the PDF generators and the deck builder as the open-source stand-in for\n"
              f" * Neue Haas Grotesk Display. AGPL 3 with URW's font exception: see assets/fonts/LICENSE-NimbusSans.txt.\n"
              f" * The build script and the built fonts: {PUBLIC_REPO}\n"
              f" * Do not edit manually; rebuild with the script.\n *\n"
              f" * Generated: {stamp}\n */\n\nwindow.{name} = '{b64}';\n")
        (OUT_DIR / f"{name}.js").write_text(js)
    lic = ("Nimbus Sans TVPM is built from URW++'s Nimbus Sans (urw-base35-fonts, commit " + COMMIT + ") by\n"
           "tools/fonts/build-nimbus.py. The script and the fonts it builds are published at\n"
           + PUBLIC_REPO + "\n"
           "The build changes the Bold's weight, both weights' spacing and word space, scales the Bold's kerning,\n"
           "adds pnum, tnum and case features, sets the embedding permission to installable, and renames the\n"
           "family. The fonts remain under the licence below: the GNU Affero General Public License\n"
           "version 3 with URW's font exception, which permits including them in a PostScript or PDF document\n"
           "regardless of the document's licence.\n\n"
           "---- LICENSE (URW++) ----\n\n" + (src_dir / "LICENSE").read_text() +
           "\n\n---- COPYING (GNU AGPL 3) ----\n\n" + (src_dir / "COPYING").read_text())
    (OUT_DIR / "LICENSE-NimbusSans.txt").write_text(lic)


# ---------- check ----------
def check(paths: dict):
    heading = "Owner Report · Riverside Flats"
    for style, path in paths.items():
        t = TTFont(path)
        gs, cmap, k = t.getGlyphSet(), t.getBestCmap(), 1000 / t["head"].unitsPerEm

        def box(ch):
            bp = BoundsPen(gs); gs[cmap[ord(ch)]].draw(bp); x0, y0, x1, y1 = bp.bounds
            return (x1 - x0) * k, (y1 - y0) * k
        hy = cmap.get(0x2212) or cmap[ord("-")]
        bp = BoundsPen(gs); gs[hy].draw(bp)
        sbs = []
        for ch in "abcdefghijklmnopqrstuvwxyz":
            g = cmap[ord(ch)]; adv = t["hmtx"][g][0]; b = BoundsPen(gs); gs[g].draw(b)
            sbs.append((b.bounds[0] + adv - b.bounds[2]) / 2 * k)
        kern_pairs = sum(len(s.kernTable) for s in t["kern"].kernTables) if "kern" in t else 0
        gsub = sorted({f.FeatureTag for f in t["GSUB"].table.FeatureList.FeatureRecord}) if "GSUB" in t else []
        gpos = sorted({f.FeatureTag for f in t["GPOS"].table.FeatureList.FeatureRecord}) if "GPOS" in t else []
        hm = t["hmtx"]; w = sum(hm[cmap[ord(c)]][0] for c in heading if ord(c) in cmap) / t["head"].unitsPerEm
        print(f"{FAMILY} {style} — {Path(path).name}")
        print(f"  stem of I {box('I')[0]:.0f} · hyphen thickness {(bp.bounds[3]-bp.bounds[1])*k:.0f} · cap height {box('H')[1]:.0f} · x-height {box('x')[1]:.0f}  (per 1,000 em)")
        print(f"  mean side bearing a–z {sum(sbs)/len(sbs):.1f} · word space {hm[cmap[32]][0]*k:.0f} · heading set width {w:.2f} em")
        print(f"  glyphs {len(t.getGlyphOrder())} · kern table pairs {kern_pairs} · GPOS {gpos} · GSUB {gsub}")
        print(f"  expect: Bold stem 178±2, hyphen 128±3, sb ≈21, space 160, heading ≈13.9; Regular stem 94, sb ≈31, space 165, heading ≈12.7")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="an existing urw-base35-fonts checkout (skips the fetch)")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    if a.check_only:
        check({"Bold": BUILD / "NimbusSansTVPM-Bold.ttf", "Regular": BUILD / "NimbusSansTVPM-Regular.ttf"}); return
    tmp = None
    if a.source:
        src = Path(a.source)
    else:
        tmp = tempfile.mkdtemp(prefix="urw-")
        src = Path(tmp) / "urw-base35-fonts"
        subprocess.run(["git", "clone", "--quiet", REPO, str(src)], check=True)
        subprocess.run(["git", "-C", str(src), "checkout", "--quiet", COMMIT], check=True)
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != COMMIT:
        sys.exit(f"source is at {head}, expected {COMMIT}")
    built = build(src)
    emit(built, src)
    check(built)
    print("wrote", ", ".join(str(p.relative_to(ROOT)) for p in built.values()), "and the two loaders + licence in", OUT_DIR.relative_to(ROOT))
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

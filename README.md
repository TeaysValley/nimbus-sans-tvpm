# Nimbus Sans TVPM

Nimbus Sans TVPM is the heading face of the PDFs and slide decks that Teays Valley Property Management's tools generate. It is URW++'s Nimbus Sans, from [urw-base35-fonts](https://github.com/ArtifexSoftware/urw-base35-fonts) at commit `3c0ba3b5687632dfc66526544a4e811fe0ec0cd9`, rebuilt by one script, `tools/fonts/build-nimbus.py`. The build makes the Bold heavier (every outline pushed out 29 units sideways and 12 up and down, of 2,048 per em, with the cap height held), pulls both weights' side bearings and word space in for display sizes, carries URW's own OpenType layout and kerning over from the `.otf` files (the Bold's kerning scaled by 1.15), adds three features (`pnum`, `tnum` and `case`), sets the embedding permission to installable, and renames the family. Why: a generated document carries its fonts inside it, so its heading face has to be one whose licence allows that, and Nimbus Sans is the same 1957 Helvetica drawing, here brought to the weight and fit the headings are designed for. The script prints every number it lands on (stems, side bearings, word space, set width, glyph and kerning counts) each time it runs.

## What is here

- `tools/fonts/build-nimbus.py`: the build. It needs Python 3, `fonttools`, `skia-pathops`, git and the network (it fetches URW's fonts at the pinned commit). `python3 tools/fonts/build-nimbus.py` builds; `--check-only` prints the numbers for fonts already built.
- `tools/fonts/build/NimbusSansTVPM-Regular.ttf` and `NimbusSansTVPM-Bold.ttf`: the fonts it builds.
- `assets/fonts/LICENSE-NimbusSans.txt`: the licence.

This repository is a mirror of the build, published so that anyone who receives one of the fonts, or a document that carries one, can get the script that made it. The script is kept in TVPM's own tools repository and copied here when it changes.

## Licence

GNU Affero General Public License version 3, with URW++'s font exception, which permits including the fonts in a PostScript or PDF document regardless of that document's licence. The full text is in `assets/fonts/LICENSE-NimbusSans.txt`. Nimbus Sans is a work of URW++ Design & Development; the changes are described above.

"""M2 checkpoint. prints raw arabic names next to what they normalize to.

the brief says show me a table of 20 raw arabic names and their normalized
forms and i want to eyeball it. so that is what this does. run it with

    python scripts/m2_checkpoint.py
"""

from __future__ import annotations

from src.lang import ArabicAdapter

ar = ArabicAdapter()

# real names and places from the corpus plus a few written the awkward way on
# purpose so the folding rules actually show up in the output
NAMES = [
    "مُحَمَّد",
    "مـحـمـد",
    "إبراهيم",
    "أحمد",
    "آمال",
    "مصطفى",
    "يحيى",
    "فاطمة",
    "عائشة",
    "الأسد",
    "السيسي",
    "أردوغان",
    "نتنياهو",
    "عبد الله",
    "الحريري",
    "زيلينسكي",
    "بشّار",
    "قَطَر",
    "المملكة العربية السعودية",
    "الجزائر",
]


def main() -> None:
    width = 32
    print(f"{'raw':<{width}} {'normalized':<{width}} tokens")
    print("=" * (width * 2 + 20))
    for name in NAMES:
        normalized = ar.normalize(name)
        tokens = len(ar.tokenize(name))
        # arabic prints right to left in a terminal so the columns look off
        # no matter what i do here. the point is the before and after pair
        print(f"{name:<{width}} {normalized:<{width}} {tokens}")

    print()
    print("blocking keys for الأسد")
    for key in sorted(ar.blocking_keys("بشار الأسد")):
        print("   ", key)

    print()
    print("romanizations")
    for name in ("محمد", "عبدالله", "زيلينسكي"):
        print(f"    {name} -> {ar.romanize(name)}")


if __name__ == "__main__":
    main()

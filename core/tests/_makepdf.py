"""A small, valid PDF writer.

No dependency, because the point is a file JARVIS can read, not a
typesetting engine. Base-14 Helvetica, one content stream per page,
a correct xref table -- which is what pypdf needs to extract the text.
"""
import sys
from pathlib import Path

LEADING = 14
TOP = 780
LEFT = 56
BOTTOM = 56
WIDTH, HEIGHT = 595, 842          # A4 in points
MAX_CHARS = 88                    # at 10pt Helvetica, comfortably inside


def escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def wrap(line: str, width: int = MAX_CHARS) -> list[str]:
    if len(line) <= width:
        return [line]
    indent = len(line) - len(line.lstrip(" "))
    out, current = [], " " * indent
    for word in line.split():
        candidate = (current + " " + word) if current.strip() else (current + word)
        if len(candidate) > width and current.strip():
            out.append(current)
            current = " " * indent + word
        else:
            current = candidate
    if current.strip():
        out.append(current)
    return out


def paginate(text: str) -> list[list[tuple[str, bool]]]:
    """Lines per page, each flagged bold or not."""
    rows: list[tuple[str, bool]] = []
    for raw in text.split("\n"):
        bold = raw.strip().isupper() and raw.strip() != ""
        for piece in wrap(raw.rstrip()):
            rows.append((piece, bold))

    per_page = (TOP - BOTTOM) // LEADING
    return [rows[i:i + per_page] for i in range(0, len(rows), per_page)] or [[]]


def content_stream(rows: list[tuple[str, bool]]) -> bytes:
    parts = ["BT", f"1 0 0 1 {LEFT} {TOP} Tm", f"{LEADING} TL"]
    for line, bold in rows:
        font = "/F2" if bold else "/F1"
        parts.append(f"{font} 10 Tf")
        parts.append(f"({escape(line)}) Tj" if line.strip() else "()Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1", "replace")


def build(text: str, out: Path) -> None:
    pages = paginate(text)
    n_pages = len(pages)

    # Object numbering: 1 catalog, 2 pages, 3 F1, 4 F2,
    # then per page: page object and its content stream.
    first_page_obj = 5
    objects: dict[int, bytes] = {}

    kids = " ".join(f"{first_page_obj + 2 * i} 0 R" for i in range(n_pages))
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (f"<< /Type /Pages /Count {n_pages} /Kids [{kids}] >>"
                  ).encode("latin-1")
    objects[3] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                  b"/Encoding /WinAnsiEncoding >>")
    objects[4] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                  b"/Encoding /WinAnsiEncoding >>")

    for i, rows in enumerate(pages):
        page_no = first_page_obj + 2 * i
        stream_no = page_no + 1
        objects[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {WIDTH} {HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {stream_no} 0 R >>"
        ).encode("latin-1")
        body = content_stream(rows)
        objects[stream_no] = (
            f"<< /Length {len(body)} >>\nstream\n".encode("latin-1")
            + body + b"\nendstream"
        )

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(pdf)
        pdf += f"{number} 0 obj\n".encode("latin-1")
        pdf += objects[number]
        pdf += b"\nendobj\n"

    highest = max(objects)
    xref_at = len(pdf)
    pdf += f"xref\n0 {highest + 1}\n".encode("latin-1")
    pdf += b"0000000000 65535 f \n"
    for number in range(1, highest + 1):
        pdf += f"{offsets[number]:010d} 00000 n \n".encode("latin-1")
    pdf += (f"trailer\n<< /Size {highest + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode("latin-1")

    out.write_bytes(bytes(pdf))


if __name__ == "__main__":
    build(Path(sys.argv[1]).read_text(), Path(sys.argv[2]))
    print(f"wrote {sys.argv[2]}")

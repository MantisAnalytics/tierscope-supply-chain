import pytest

from scgraph.ingest.documents import TextBlock, chunks, find_quote, read_document

TEXT = """Report

Site X1 is Plant One. Its city is Bremen.
It is a large site.

Supplier S1 is Acme GmbH.

""" + "\n".join(f"Line {i} of a long paragraph without blank lines." for i in range(30)) + "\n"


def test_chunks_keep_lines_whole_and_map_back(tmp_path):
    path = tmp_path / "r.txt"
    path.write_text(TEXT)
    doc = read_document(path)
    [block] = doc.blocks
    parts = chunks(block, max_chars=300)
    assert len(parts) > 2
    lines = set(TEXT.splitlines())
    for c in parts:
        assert block.text[c.start:c.start + len(c.text)] == c.text
        for line in c.text.splitlines():
            assert line in lines or not line.strip()  # no line is cut
    span = find_quote(block.text, "Supplier S1 is Acme GmbH.")
    loc = doc.location(block, *span, quote="Supplier S1 is Acme GmbH.")
    assert loc.lines == (6, 6) and loc.file == "r.txt" and loc.quote == "Supplier S1 is Acme GmbH."
    two = find_quote(block.text, "Its city is Bremen. It is")
    assert doc.location(block, *two).lines == (3, 4)


def test_paragraphs_packed_up_to_limit():
    block = TextBlock("A a a.\n\nB b b.\n\nC c c.\n")
    assert [c.text for c in chunks(block, max_chars=14)] == ["A a a.\n\nB b b.", "C c c."]
    assert [c.text for c in chunks(block)] == ["A a a.\n\nB b b.\n\nC c c."]


def test_find_quote_tolerates_whitespace_and_typographic_quotes():
    text = "He said “ship   it”\nnow."
    assert find_quote(text, '"ship it" now.') == (8, len(text))
    assert find_quote(text, "ship it later") is None


def test_unsupported_document(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        read_document(tmp_path / "x.docx")

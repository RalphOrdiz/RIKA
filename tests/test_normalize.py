import pytest

from rika.normalize import normalize_filename, split_ext, squash


@pytest.mark.parametrize(
    "spoken, expected",
    [
        # the basics from the spec
        ("document one dot pdf", "document1.pdf"),
        ("document1.pdf", "document1.pdf"),
        ("Document1.pdf.", "document1.pdf"),
        ("document 1.PDF", "document1.pdf"),
        ("document one dot docx", "document1.docx"),
        ("report dot docx", "report.docx"),
        ("report dot doc", "report.doc"),
        # docx spoken in pieces / mis-heard
        ("report dot doc x", "report.docx"),
        ("report dot docks", "report.docx"),
        ("report dot dox", "report.docx"),
        ("report dot dock", "report.doc"),
        ("report dot p d f", "report.pdf"),
        ("report point pdf", "report.pdf"),
        ("report period pdf", "report.pdf"),
        # extension without "dot"
        ("report pdf", "report.pdf"),
        ("report docx", "report.docx"),
        ("thesis final docx", "thesis final.docx"),
        # numbers
        ("invoice twenty twenty four dot pdf", "invoice2024.pdf"),
        ("scan zero zero one dot pdf", "scan001.pdf"),
        ("form one two three dot pdf", "form123.pdf"),
        ("page twenty one dot pdf", "page21.pdf"),
        ("one hundred five dot doc", "105.doc"),
        ("two thousand twenty four report.pdf", "2024 report.pdf"),
        ("nineteen ninety nine dot docx", "1999.docx"),
        ("chapter three dot docx", "chapter3.docx"),
        ("module eleven dot pdf", "module11.pdf"),
        ("lesson fifteen dot pdf", "lesson15.pdf"),
        ("document 12.pdf", "document12.pdf"),
        ("document for dot pdf", "document4.pdf"),
        ("document to dot pdf", "document2.pdf"),
        # underscore / dash
        ("my underscore resume dot pdf", "my_resume.pdf"),
        ("my under score resume dot pdf", "my_resume.pdf"),
        ("my underscore resume dash final dot doc x", "my_resume-final.docx"),
        ("report hyphen v two dot pdf", "report-v2.pdf"),
        ("id dash front dot pdf", "id-front.pdf"),
        ("form_1.pdf", "form_1.pdf"),
        ("form-a.docx", "form-a.docx"),
        # spelled letters
        ("j u a n dot pdf", "juan.pdf"),
        ("c v dot pdf", "cv.pdf"),
        # fillers
        ("the file called final report dot docx", "final report.docx"),
        ("the file named document one dot pdf", "document1.pdf"),
        ("the attachment called thesis chapter three dot docx", "thesis chapter3.docx"),
        ("document1.pdf from gmail please", "document1.pdf"),
        ("document1.pdf for me", "document1.pdf"),
        ("the resume dot pdf right now", "resume.pdf"),
        ("my report dot pdf thank you", "report.pdf"),
        # no extension spoken
        ("document one", "document1"),
        ("final report", "final report"),
        # junk / empty
        ("", ""),
        ("the", ""),
        ("  .  ", ""),
    ],
)
def test_normalize_filename(spoken, expected):
    assert normalize_filename(spoken) == expected


def test_split_ext():
    assert split_ext("Document1.PDF") == ("document1", ".pdf")
    assert split_ext("report.docx") == ("report", ".docx")
    assert split_ext("report.doc") == ("report", ".doc")
    assert split_ext("photo.jpg") == ("photo.jpg", "")
    assert split_ext("document1") == ("document1", "")


def test_squash():
    assert squash("Document 1.pdf") == "document1pdf"
    assert squash("RJO Printing_Service") == "rjoprintingservice"

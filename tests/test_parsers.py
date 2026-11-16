import io

from resume_tailor.parsers import ParseError, clean_text, extract_text, latex_to_text


def test_latex_to_text_jakes_template(tex_text):
    text = latex_to_text(tex_text)
    assert "## Experience" in text
    assert "### Brightline Logistics | Jun 2021 - Present | Software Engineer | Austin, TX" in text
    assert "- Worked on migrating cron jobs to AWS Lambda; reduced cost by 30%." in text
    assert "alex.morgan@example.com" in text
    assert "Languages: Python, JavaScript, SQL" in text
    # No LaTeX leftovers
    assert "\\" not in text and "{" not in text and "[t]" not in text


def test_extract_docx_keeps_bullets():
    import docx

    doc = docx.Document()
    doc.add_paragraph("Jane Doe")
    doc.add_paragraph("EXPERIENCE")
    doc.add_paragraph("Built data pipelines processing 2M rows per day with Airflow and Python.", style="List Bullet")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Skills"
    table.rows[0].cells[1].text = "Python, SQL"
    buf = io.BytesIO()
    doc.save(buf)
    text = extract_text("cv.docx", buf.getvalue())
    assert "- Built data pipelines processing 2M rows" in text
    assert "Skills | Python, SQL" in text


def test_unsupported_and_empty_files():
    for name, data in [("cv.png", b"x" * 100), ("cv.txt", b"")]:
        try:
            extract_text(name, data)
        except ParseError:
            continue
        raise AssertionError(f"{name} should fail")


def test_clean_text_normalizes_dashes_and_spaces():
    assert clean_text("Jan 2020 \u2013 Present\u00a0\u00a0 now") == "Jan 2020 - Present now"

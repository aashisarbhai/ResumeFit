import html
import logging
import re

try:
    from weasyprint import HTML
    WEASYPRINT_INSTALLED = True
except (ImportError, OSError):
    WEASYPRINT_INSTALLED = False

logger = logging.getLogger('ats_resume_scorer')


def _basic_pdf(html_docs: dict[str, str]) -> bytes:
    """Create a readable PDF even on hosts without WeasyPrint system libraries."""
    source = '\n\n'.join(html_docs.values())
    plain_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(source))).strip()
    lines = [plain_text[index:index + 92] for index in range(0, len(plain_text), 92)] or ['ATS Resume Report']
    lines = lines[:55]
    commands = ['BT', '/F1 10 Tf', '50 790 Td', '14 TL']
    for line in lines:
        escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        commands.extend([f'({escaped}) Tj', 'T*'])
    commands.append('ET')
    stream = '\n'.join(commands).encode('latin-1', 'replace')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
    ]
    output = bytearray(b'%PDF-1.4\n')
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref_offset = len(output)
    output.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    output.extend(b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets))
    output.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF'.encode())
    return bytes(output)


def generate_combined_pdf(html_docs: dict[str, str]) -> bytes:
    if not WEASYPRINT_INSTALLED:
        logger.warning('WeasyPrint is unavailable; using the built-in PDF fallback.')
        return _basic_pdf(html_docs)
    try:
        documents = [HTML(string=content).render() for content in html_docs.values()]
        if not documents:
            return _basic_pdf({'report': 'ATS Resume Report'})
        first_doc = documents[0]
        for other_doc in documents[1:]:
            first_doc.pages.extend(other_doc.pages)
        return first_doc.write_pdf()
    except Exception as exc:
        logger.warning('WeasyPrint failed; using the built-in PDF fallback: %s', exc)
        return _basic_pdf(html_docs)

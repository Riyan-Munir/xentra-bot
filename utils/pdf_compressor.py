"""
PDF Compression Utility
=======================
Compresses PDF bytes in-memory using ``pypdf`` (stream compression).
Always attempts compression and returns the smaller of original vs compressed.

Usage::

    from utils.pdf_compressor import compress_pdf

    compressed = compress_pdf(raw_bytes)
    # if compression doesn't help, raw_bytes is returned unchanged.
"""

import io
import logging
from typing import Optional

logger = logging.getLogger('bot.utils.pdf_compressor')

# Discord bot file-upload limit (conservative — leave 1 MB headroom)
DISCORD_MAX_BYTES = 24 * 1024 * 1024


def compress_pdf(
    pdf_bytes: bytes,
    max_size: int = DISCORD_MAX_BYTES,
    max_pass: int = 3,
) -> bytes:
    """
    Attempt to compress *pdf_bytes* with ``pypdf``.

    Always tries compression and returns whichever is smaller (original vs
    compressed).  If *compressed* still exceeds *max_size* after all passes,
    the *best* (smallest) result is returned rather than failing.

    Strategy (escalating):
      1. Lossless content-stream compression only (fast).
      2. If still too large, re-compress object streams.
      3. If still too large, remove all images (lossy — last resort).

    Parameters
    ----------
    pdf_bytes:
        Raw PDF file bytes.
    max_size:
        Target size in bytes (default 24 MB).  Used only for pass decisions.
    max_pass:
        Maximum compression passes (default 3).

    Returns
    -------
    Compressed PDF bytes (may be identical if compression doesn't help).
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        logger.warning('pypdf not installed — returning original PDF bytes')
        return pdf_bytes

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()

        # Copy all pages
        for page in reader.pages:
            writer.add_page(page)

        # Pass 1: lossless compression
        writer.compress_content_streams = True

        buf = io.BytesIO()
        writer.write(buf)
        compressed = buf.getvalue()
        best = compressed if len(compressed) < len(pdf_bytes) else pdf_bytes
        logger.info(
            'PDF compression pass 1: %d → %d bytes (%.1f%% reduction)',
            len(pdf_bytes), len(compressed),
            (1 - len(compressed) / len(pdf_bytes)) * 100,
        )

        if len(best) <= max_size:
            return best

        # Pass 2: re-compress object streams (sometimes helps further)
        if max_pass >= 2:
            writer2 = PdfWriter()
            for page in writer.pages:
                writer2.add_page(page)
            buf2 = io.BytesIO()
            writer2.write(buf2)
            compressed2 = buf2.getvalue()
            logger.info(
                'PDF compression pass 2: %d → %d bytes (%.1f%%)',
                len(best), len(compressed2),
                (1 - len(compressed2) / len(best)) * 100,
            )
            if len(compressed2) < len(best):
                best = compressed2

        if len(best) <= max_size:
            return best

        # Pass 3 (lossy): remove images from all pages
        if max_pass >= 3:
            logger.warning(
                'PDF still %d bytes after lossless passes — removing images',
                len(best),
            )
            writer3 = PdfWriter()
            for page in reader.pages:
                writer3.add_page(page)
                _remove_images_from_page(page)
            buf3 = io.BytesIO()
            writer3.write(buf3)
            compressed3 = buf3.getvalue()
            logger.info(
                'PDF compression pass 3 (no images): %d → %d bytes (%.1f%%)',
                len(best), len(compressed3),
                (1 - len(compressed3) / len(best)) * 100,
            )
            if len(compressed3) < len(best):
                best = compressed3

        return best

    except Exception:
        logger.exception('PDF compression failed — returning original bytes')
        return pdf_bytes


def _remove_images_from_page(page) -> None:
    """Remove image XObjects from a PDF page (lossy)."""
    try:
        from pypdf.generic import ArrayObject, DictionaryObject
    except ImportError:
        return

    try:
        resources = page.get('/Resources')
        if resources is None:
            return
        xobject = resources.get('/XObject')
        if xobject is None:
            return

        to_delete = []
        for key, obj in xobject.items():
            try:
                if obj.get('/Subtype') == '/Image':
                    to_delete.append(key)
            except Exception:
                pass

        for key in to_delete:
            del xobject[key]

        if to_delete:
            logger.info('Removed %d image(s) from page', len(to_delete))
    except Exception:
        logger.debug('Failed to remove images from PDF page', exc_info=True)

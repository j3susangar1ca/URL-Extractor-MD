"""RAG chunking and Markdown document building.

This module provides the RAGChunker for splitting extraction results
into word-count-bounded chunks, and the MarkdownBuilder for generating
structured Markdown documents with YAML frontmatter.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..domain.models import ExtractionResult, PreviewResult, RAGChunk

# ────────────────────────────────────────────────────────
#  Constants
# ────────────────────────────────────────────────────────

CHUNK_MIN_WORDS: int = 500
CHUNK_MAX_WORDS: int = 800

_RE_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_RE_FOUR_BLANKS = re.compile(r"\n{4,}")
_RE_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")


# ────────────────────────────────────────────────────────
#  RAGChunker
# ────────────────────────────────────────────────────────


class RAGChunker:
    """Split an ExtractionResult into RAG-ready word-chunks.

    Chunks respect paragraph boundaries where possible and fall back to
    sentence-level splitting for oversized paragraphs.

    Attributes:
        min_words: Minimum words per chunk.
        max_words: Maximum words per chunk.
    """

    def __init__(
        self,
        min_words: int = CHUNK_MIN_WORDS,
        max_words: int = CHUNK_MAX_WORDS,
    ) -> None:
        self.min_words = min_words
        self.max_words = max_words

    def chunk(self, result: ExtractionResult) -> list[RAGChunk]:
        """Chunk an extraction result into RAG-ready segments.

        The algorithm:
        1. Split Markdown text into paragraphs (``\\n\\n`` delimiter).
        2. Accumulate paragraphs until ``min_words`` is reached.
        3. If a single paragraph exceeds ``max_words``, split at
           sentence boundaries.
        4. Emit a new RAGChunk when the accumulation crosses
           ``min_words`` or exceeds ``max_words``.

        Args:
            result: A fully populated extraction result.

        Returns:
            List of RAGChunk instances with auto-generated IDs.
        """
        paragraphs = [
            p.strip() for p in result.clean_markdown.split("\n\n") if p.strip()
        ]
        chunks: list[RAGChunk] = []
        buffer: list[str] = []
        buffer_words = 0
        idx = 0

        for para in paragraphs:
            words = para.split()
            para_wc = len(words)

            # Oversized paragraph — sentence-split
            if para_wc > self.max_words:
                if buffer:
                    chunks.append(self._make_chunk(idx, buffer, result))
                    idx += 1
                    buffer = []
                    buffer_words = 0

                sub_chunks = self._split_sentences(para)
                for sub in sub_chunks:
                    sub_wc = len(sub.split())
                    if sub_wc == 0:
                        continue
                    chunks.append(self._make_chunk(idx, [sub], result))
                    idx += 1
                continue

            # Would adding this paragraph exceed max?
            if buffer_words + para_wc > self.max_words and buffer:
                chunks.append(self._make_chunk(idx, buffer, result))
                idx += 1
                buffer = []
                buffer_words = 0

            buffer.append(para)
            buffer_words += para_wc

            if buffer_words >= self.min_words:
                chunks.append(self._make_chunk(idx, buffer, result))
                idx += 1
                buffer = []
                buffer_words = 0

        # Flush remaining buffer
        if buffer:
            chunks.append(self._make_chunk(idx, buffer, result))

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """Split an oversized paragraph into sentence-level sub-strings.

        Each sub-string is grown until it reaches ``min_words`` or
        runs out of sentences.

        Args:
            text: Paragraph text.

        Returns:
            List of sentence-grouped sub-strings.
        """
        sentences = _RE_SENTENCE_END.split(text)
        result: list[str] = []
        buf: list[str] = []
        buf_wc = 0

        for sent in sentences:
            wc = len(sent.split())
            if buf_wc + wc > self.max_words and buf:
                result.append(" ".join(buf))
                buf = []
                buf_wc = 0
            buf.append(sent)
            buf_wc += wc
            if buf_wc >= self.min_words:
                result.append(" ".join(buf))
                buf = []
                buf_wc = 0

        if buf:
            result.append(" ".join(buf))

        return result

    @staticmethod
    def _make_chunk(
        index: int, paragraphs: list[str], result: ExtractionResult
    ) -> RAGChunk:
        """Create a RAGChunk from accumulated paragraphs.

        Args:
            index: Zero-based chunk index.
            paragraphs: List of paragraph strings.
            result: Parent extraction result (for URL/title).

        Returns:
            A new RAGChunk.
        """
        content = "\n\n".join(paragraphs)
        return RAGChunk(
            index=index,
            content=content,
            word_count=len(content.split()),
            source_url=result.metadata.url,
            source_title=result.metadata.title,
        )


# ────────────────────────────────────────────────────────
#  RAGPipeline (preview helper)
# ────────────────────────────────────────────────────────


class RAGPipeline:
    """High-level processing pipeline helper for metadata previews."""

    @staticmethod
    def preview(result: ExtractionResult) -> PreviewResult:
        """Create a dry-run preview from the extraction result.

        Args:
            result: Fully populated extraction result.

        Returns:
            PreviewResult with metadata and projected chunk count.
        """
        chunker = RAGChunker()
        chunks = chunker.chunk(result)
        return PreviewResult(
            title=result.metadata.title,
            author=result.metadata.author,
            site_name=result.metadata.site_name,
            description=result.metadata.description,
            content_type=result.metadata.content_type.value,
            language=result.metadata.language,
            published_date=result.metadata.published_date,
            keywords=result.metadata.keywords,
            estimated_word_count=result.word_count,
            projected_chunks=len(chunks),
        )


# ────────────────────────────────────────────────────────
#  MarkdownBuilder
# ────────────────────────────────────────────────────────


class MarkdownBuilder:
    """Build structured Markdown documents from extraction results.

    Two output formats are supported:
    * Full document with YAML frontmatter and sections.
    * Chunk-specific document for vector-store ingestion.
    """

    def build_document(
        self, result: ExtractionResult, user_filename: str | None = None
    ) -> str:
        """Build a full Markdown document with YAML frontmatter.

        Document structure:
        1. YAML frontmatter (metadata_version, timestamp, source_url, etc.)
        2. Title heading
        3. Resumen section
        4. Contenido extraido section
        5. Enlaces de referencia section

        Args:
            result: Fully populated extraction result.
            user_filename: Optional user-supplied filename for frontmatter.

        Returns:
            Complete Markdown string.
        """
        from ..infrastructure.extraction.slug_generator import SlugGenerator

        meta = result.metadata
        now = datetime.now(tz=timezone.utc).isoformat()

        frontmatter = (
            f"---\n"
            f'metadata_version: "1.0"\n'
            f"timestamp: {_yaml_escape(now)}\n"
            f"source_url: {_yaml_escape(meta.url)}\n"
            f"site_name: {_yaml_escape(meta.site_name)}\n"
            f"author: {_yaml_escape(meta.author)}\n"
            f"content_type: {_yaml_escape(meta.content_type.value)}\n"
            f"language: {_yaml_escape(meta.language)}\n"
            f"published_date: {_yaml_escape(meta.published_date)}\n"
            f"description: {_yaml_escape(meta.description)}\n"
            f"keywords: [{', '.join(_yaml_escape(k) for k in meta.keywords)}]\n"
            f"word_count: {result.word_count}\n"
            f"paragraph_count: {result.paragraph_count}\n"
            f"extraction_status: {_yaml_escape(result.status.value)}\n"
        )
        if user_filename:
            frontmatter += f"user_filename: {_yaml_escape(user_filename)}\n"
        frontmatter += "---\n\n"

        title = meta.title or SlugGenerator.from_url(meta.url)
        doc = f"{frontmatter}# {title}\n\n"

        # Resumen
        summary = meta.description or "(Sin descripcion disponible)"
        doc += f"## Resumen\n\n{summary}\n\n"

        # Contenido extraido
        doc += f"## Contenido extraido\n\n{result.clean_markdown}\n\n"

        # Enlaces de referencia
        doc += "## Enlaces de referencia\n\n"
        if result.links_found:
            doc += "### Enlaces\n\n"
            for link in result.links_found[:50]:
                doc += f"- {_yaml_escape(link)}\n"
            doc += "\n"
        if result.images_found:
            doc += "### Imagenes\n\n"
            for img in result.images_found[:30]:
                doc += f"- {_yaml_escape(img)}\n"
            doc += "\n"

        if not result.links_found and not result.images_found:
            doc += "(Sin enlaces de referencia)\n"

        return doc

    def build_chunk_document(self, chunk: RAGChunk, total_chunks: int) -> str:
        """Build a Markdown document for a single RAG chunk.

        Args:
            chunk: The RAGChunk to render.
            total_chunks: Total number of chunks in the parent document.

        Returns:
            Markdown string with chunk metadata and content.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        return (
            f"---\n"
            f'metadata_version: "1.0"\n'
            f"timestamp: {_yaml_escape(now)}\n"
            f"source_url: {_yaml_escape(chunk.source_url)}\n"
            f"source_title: {_yaml_escape(chunk.source_title)}\n"
            f"chunk_index: {chunk.index}\n"
            f"total_chunks: {total_chunks}\n"
            f"chunk_id: {_yaml_escape(chunk.chunk_id)}\n"
            f"word_count: {chunk.word_count}\n"
            f"---\n\n"
            f"# Chunk {chunk.index + 1}/{total_chunks}\n\n"
            f"{chunk.content}\n"
        )


# ────────────────────────────────────────────────────────
#  YAML escaping utility
# ────────────────────────────────────────────────────────


def _yaml_escape(text: str) -> str:
    """Escape a string for safe embedding in a YAML double-quoted value.

    Handles characters YAML requires escaped inside double-quoted scalars:
    backslash, double-quote, newline, carriage return, and tab.

    Args:
        text: Raw string to escape.

    Returns:
        YAML-safe string enclosed in double quotes.
    """
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'

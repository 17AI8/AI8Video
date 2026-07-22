from __future__ import annotations


def schema_statements() -> tuple[str, ...]:
    return (
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        """
        CREATE TABLE IF NOT EXISTS ai8_script_documents (
            id BIGSERIAL PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            stem TEXT NOT NULL,
            source_path TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash CHAR(64) NOT NULL,
            preview TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            tags TEXT[] NOT NULL DEFAULT '{}',
            metadata JSONB NOT NULL DEFAULT '{}',
            size_bytes BIGINT NOT NULL DEFAULT 0,
            source_modified_at DOUBLE PRECISION NOT NULL DEFAULT 0,
            index_status TEXT NOT NULL DEFAULT 'ready',
            index_version INTEGER NOT NULL DEFAULT 1,
            indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai8_script_sections (
            id BIGSERIAL PRIMARY KEY,
            document_id BIGINT NOT NULL REFERENCES ai8_script_documents(id) ON DELETE CASCADE,
            section_order INTEGER NOT NULL,
            heading TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            char_count INTEGER NOT NULL DEFAULT 0,
            search_terms TEXT NOT NULL,
            search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', search_terms)) STORED,
            UNIQUE(document_id, section_order)
        )
        """,
        "ALTER TABLE ai8_script_documents ADD COLUMN IF NOT EXISTS index_version INTEGER NOT NULL DEFAULT 1",
        "UPDATE ai8_script_documents SET title = stem WHERE title = ''",
        "CREATE INDEX IF NOT EXISTS ai8_script_documents_tags_idx ON ai8_script_documents USING GIN(tags)",
        "CREATE INDEX IF NOT EXISTS ai8_script_documents_name_trgm_idx ON ai8_script_documents USING GIN(name gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ai8_script_documents_title_trgm_idx ON ai8_script_documents USING GIN(title gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_document_idx ON ai8_script_sections(document_id, section_order)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_heading_trgm_idx ON ai8_script_sections USING GIN(heading gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_vector_idx ON ai8_script_sections USING GIN(search_vector)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_content_trgm_idx ON ai8_script_sections USING GIN(content gin_trgm_ops)",
    )


def upsert_document_sql() -> str:
    return """
        INSERT INTO ai8_script_documents (
            relative_path, name, stem, source_path, content_type, content, content_hash,
            preview, size_bytes, source_modified_at, index_status, index_version, indexed_at, title
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        ON CONFLICT (relative_path) DO UPDATE SET
            name = EXCLUDED.name,
            stem = EXCLUDED.stem,
            source_path = EXCLUDED.source_path,
            content_type = EXCLUDED.content_type,
            content = EXCLUDED.content,
            content_hash = EXCLUDED.content_hash,
            preview = EXCLUDED.preview,
            size_bytes = EXCLUDED.size_bytes,
            source_modified_at = EXCLUDED.source_modified_at,
            index_status = EXCLUDED.index_status,
            index_version = EXCLUDED.index_version,
            title = CASE
                WHEN ai8_script_documents.title = '' THEN EXCLUDED.title
                ELSE ai8_script_documents.title
            END,
            indexed_at = NOW(),
            updated_at = NOW()
        RETURNING id
    """


def document_list_sql() -> str:
    return """
        SELECT d.*, COUNT(s.id)::INTEGER AS section_count
        FROM ai8_script_documents d
        LEFT JOIN ai8_script_sections s ON s.document_id = d.id
        GROUP BY d.id
        ORDER BY d.updated_at DESC, d.id DESC
        LIMIT %s
    """


def document_detail_sql() -> str:
    return """
        SELECT d.*, COUNT(s.id)::INTEGER AS section_count
        FROM ai8_script_documents d
        LEFT JOIN ai8_script_sections s ON s.document_id = d.id
        WHERE d.id = %s
        GROUP BY d.id
    """


def search_sql() -> str:
    return """
        WITH query_input AS (
            SELECT %s::TEXT AS raw_query, %s::TEXT AS like_pattern,
                   to_tsquery('simple', %s) AS ts_query
        )
        SELECT d.*, s.id AS matched_section_id, s.heading AS matched_heading,
               LEFT(s.content, 420) AS matched_excerpt,
               (
                   CASE WHEN LOWER(d.name) = LOWER(q.raw_query) THEN 8 ELSE 0 END +
                   CASE WHEN d.name ILIKE q.like_pattern ESCAPE '\\' THEN 4 ELSE 0 END +
                   CASE WHEN d.title ILIKE q.like_pattern ESCAPE '\\' THEN 3 ELSE 0 END +
                   CASE WHEN s.heading ILIKE q.like_pattern ESCAPE '\\' THEN 5 ELSE 0 END +
                   CASE WHEN s.content ILIKE q.like_pattern ESCAPE '\\' THEN 2 ELSE 0 END +
                   similarity(d.name, q.raw_query) * 2 +
                   similarity(d.title, q.raw_query) * 1.5 +
                   similarity(s.heading, q.raw_query) * 2.5 +
                   ts_rank_cd(s.search_vector, q.ts_query) * 4
               )::DOUBLE PRECISION AS score,
               (SELECT COUNT(*)::INTEGER FROM ai8_script_sections section_total
                WHERE section_total.document_id = d.id) AS section_count
        FROM ai8_script_documents d
        JOIN ai8_script_sections s ON s.document_id = d.id
        CROSS JOIN query_input q
        WHERE d.index_status = 'ready' AND (
            s.search_vector @@ q.ts_query OR
            d.name ILIKE q.like_pattern ESCAPE '\\' OR
            d.title ILIKE q.like_pattern ESCAPE '\\' OR
            s.heading ILIKE q.like_pattern ESCAPE '\\' OR
            d.summary ILIKE q.like_pattern ESCAPE '\\' OR
            s.content ILIKE q.like_pattern ESCAPE '\\' OR
            array_to_string(d.tags, ' ') ILIKE q.like_pattern ESCAPE '\\' OR
            similarity(d.name, q.raw_query) >= 0.16 OR
            similarity(d.title, q.raw_query) >= 0.16 OR
            similarity(s.heading, q.raw_query) >= 0.16
        )
        ORDER BY score DESC, d.updated_at DESC
        LIMIT %s
    """


def section_search_sql() -> str:
    return """
        WITH query_input AS (
            SELECT %s::TEXT AS raw_query, %s::TEXT AS like_pattern,
                   to_tsquery('simple', %s) AS ts_query, %s::TEXT AS relative_path
        )
        SELECT d.id AS document_id, d.name, d.title, d.relative_path,
               s.id AS section_id, s.section_order, s.heading, s.content,
               (
                   CASE WHEN LOWER(d.name) = LOWER(q.raw_query) THEN 8 ELSE 0 END +
                   CASE WHEN d.name ILIKE q.like_pattern ESCAPE '\\' THEN 4 ELSE 0 END +
                   CASE WHEN d.title ILIKE q.like_pattern ESCAPE '\\' THEN 3 ELSE 0 END +
                   CASE WHEN s.heading ILIKE q.like_pattern ESCAPE '\\' THEN 5 ELSE 0 END +
                   CASE WHEN s.content ILIKE q.like_pattern ESCAPE '\\' THEN 2 ELSE 0 END +
                   similarity(d.name, q.raw_query) * 2 +
                   similarity(d.title, q.raw_query) * 1.5 +
                   similarity(s.heading, q.raw_query) * 2.5 +
                   ts_rank_cd(s.search_vector, q.ts_query) * 4
               )::DOUBLE PRECISION AS score
        FROM ai8_script_documents d
        JOIN ai8_script_sections s ON s.document_id = d.id
        CROSS JOIN query_input q
        WHERE d.index_status = 'ready'
          AND (q.relative_path = '' OR d.relative_path = q.relative_path)
          AND (
              s.search_vector @@ q.ts_query OR
              d.name ILIKE q.like_pattern ESCAPE '\\' OR
              d.title ILIKE q.like_pattern ESCAPE '\\' OR
              s.heading ILIKE q.like_pattern ESCAPE '\\' OR
              d.summary ILIKE q.like_pattern ESCAPE '\\' OR
              s.content ILIKE q.like_pattern ESCAPE '\\' OR
              array_to_string(d.tags, ' ') ILIKE q.like_pattern ESCAPE '\\' OR
              similarity(d.name, q.raw_query) >= 0.16 OR
              similarity(d.title, q.raw_query) >= 0.16 OR
              similarity(s.heading, q.raw_query) >= 0.16
          )
        ORDER BY score DESC, s.section_order ASC
        LIMIT %s
    """

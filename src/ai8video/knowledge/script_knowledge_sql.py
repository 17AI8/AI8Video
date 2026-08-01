from __future__ import annotations


SCRIPT_KNOWLEDGE_SCHEMA_VERSION = 3


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
            source_file_hash CHAR(64) NOT NULL DEFAULT '',
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
            retrieval_text TEXT NOT NULL DEFAULT '',
            token_count INTEGER NOT NULL DEFAULT 0,
            search_terms TEXT NOT NULL,
            search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', search_terms)) STORED,
            UNIQUE(document_id, section_order)
        )
        """,
        "ALTER TABLE ai8_script_documents ADD COLUMN IF NOT EXISTS index_version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE ai8_script_documents ADD COLUMN IF NOT EXISTS source_file_hash CHAR(64) NOT NULL DEFAULT ''",
        "ALTER TABLE ai8_script_sections ADD COLUMN IF NOT EXISTS retrieval_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE ai8_script_sections ADD COLUMN IF NOT EXISTS token_count INTEGER NOT NULL DEFAULT 0",
        "UPDATE ai8_script_sections SET retrieval_text = CONCAT_WS(E'\\n', heading, content) WHERE retrieval_text = ''",
        """
        CREATE TABLE IF NOT EXISTS ai8_script_bm25_corpora (
            document_id BIGINT PRIMARY KEY REFERENCES ai8_script_documents(id) ON DELETE CASCADE,
            index_version INTEGER NOT NULL,
            tokenizer_version INTEGER NOT NULL,
            section_count INTEGER NOT NULL CHECK (section_count >= 0),
            average_section_length DOUBLE PRECISION NOT NULL CHECK (average_section_length >= 0),
            corpus_hash CHAR(64) NOT NULL,
            built_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai8_script_bm25_terms (
            document_id BIGINT NOT NULL REFERENCES ai8_script_documents(id) ON DELETE CASCADE,
            term TEXT NOT NULL CHECK (term <> ''),
            section_id BIGINT NOT NULL REFERENCES ai8_script_sections(id) ON DELETE CASCADE,
            term_frequency INTEGER NOT NULL CHECK (term_frequency > 0),
            PRIMARY KEY(document_id, term, section_id)
        )
        """,
        "UPDATE ai8_script_documents SET title = stem WHERE title = ''",
        "CREATE INDEX IF NOT EXISTS ai8_script_documents_tags_idx ON ai8_script_documents USING GIN(tags)",
        "CREATE INDEX IF NOT EXISTS ai8_script_documents_name_trgm_idx ON ai8_script_documents USING GIN(name gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ai8_script_documents_title_trgm_idx ON ai8_script_documents USING GIN(title gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_document_idx ON ai8_script_sections(document_id, section_order)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_heading_trgm_idx ON ai8_script_sections USING GIN(heading gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_vector_idx ON ai8_script_sections USING GIN(search_vector)",
        "CREATE INDEX IF NOT EXISTS ai8_script_sections_content_trgm_idx ON ai8_script_sections USING GIN(content gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ai8_script_bm25_terms_section_idx ON ai8_script_bm25_terms(section_id)",
    )


def upsert_document_sql() -> str:
    return """
        INSERT INTO ai8_script_documents (
            relative_path, name, stem, source_path, content_type, content, content_hash,
            source_file_hash, preview, size_bytes, source_modified_at, index_status,
            index_version, indexed_at, title
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        ON CONFLICT (relative_path) DO UPDATE SET
            name = EXCLUDED.name,
            stem = EXCLUDED.stem,
            source_path = EXCLUDED.source_path,
            content_type = EXCLUDED.content_type,
            content = EXCLUDED.content,
            content_hash = EXCLUDED.content_hash,
            source_file_hash = EXCLUDED.source_file_hash,
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
        SELECT d.*,
               (SELECT COUNT(*)::INTEGER FROM ai8_script_sections s
                WHERE s.document_id = d.id) AS section_count,
               c.index_version AS bm25_index_version,
               c.tokenizer_version AS bm25_tokenizer_version,
               c.section_count AS bm25_section_count,
               c.average_section_length AS bm25_average_section_length,
               c.corpus_hash AS bm25_corpus_hash,
               c.built_at AS bm25_built_at
        FROM ai8_script_documents d
        LEFT JOIN ai8_script_bm25_corpora c ON c.document_id = d.id
        ORDER BY d.updated_at DESC, d.id DESC
        LIMIT %s
    """


def document_detail_sql() -> str:
    return """
        SELECT d.*,
               (SELECT COUNT(*)::INTEGER FROM ai8_script_sections s
                WHERE s.document_id = d.id) AS section_count,
               c.index_version AS bm25_index_version,
               c.tokenizer_version AS bm25_tokenizer_version,
               c.section_count AS bm25_section_count,
               c.average_section_length AS bm25_average_section_length,
               c.corpus_hash AS bm25_corpus_hash,
               c.built_at AS bm25_built_at
        FROM ai8_script_documents d
        LEFT JOIN ai8_script_bm25_corpora c ON c.document_id = d.id
        WHERE d.id = %s
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


def bm25_document_state_sql() -> str:
    return """
        SELECT d.id AS document_id, d.relative_path, d.content_hash, d.index_status,
               c.index_version, c.tokenizer_version, c.section_count,
               c.average_section_length, c.corpus_hash, c.built_at
        FROM ai8_script_documents d
        LEFT JOIN ai8_script_bm25_corpora c ON c.document_id = d.id
        WHERE d.relative_path = %s AND d.index_status = 'ready'
    """


def bm25_backfill_candidates_sql() -> str:
    return """
        SELECT d.id
        FROM ai8_script_documents d
        LEFT JOIN ai8_script_bm25_corpora c ON c.document_id = d.id
        WHERE d.index_status = 'ready'
          AND EXISTS (
              SELECT 1 FROM ai8_script_sections s WHERE s.document_id = d.id
          )
          AND (
              c.document_id IS NULL OR
              c.index_version <> %s OR
              c.tokenizer_version <> %s
          )
        ORDER BY d.id
    """


def bm25_section_search_sql() -> str:
    return """
        WITH query_input AS (
            SELECT %s::TEXT AS raw_query, %s::TEXT AS like_pattern,
                   %s::DOUBLE PRECISION AS k1, %s::DOUBLE PRECISION AS b,
                   %s::INTEGER AS result_limit
        ),
        query_terms AS (
            SELECT DISTINCT term
            FROM UNNEST(%s::TEXT[]) AS query_term(term)
            WHERE term <> ''
        ),
        selected_corpus AS (
            SELECT d.id AS document_id, d.name, d.title, d.relative_path,
                   c.section_count::DOUBLE PRECISION AS section_count,
                   GREATEST(c.average_section_length, 1.0) AS average_section_length
            FROM ai8_script_documents d
            JOIN ai8_script_bm25_corpora c ON c.document_id = d.id
            WHERE d.id = %s AND d.index_status = 'ready'
              AND c.index_version = %s AND c.tokenizer_version = %s
        ),
        term_statistics AS (
            SELECT posting.term, COUNT(*)::DOUBLE PRECISION AS document_frequency
            FROM ai8_script_bm25_terms posting
            JOIN query_terms query_term ON query_term.term = posting.term
            JOIN selected_corpus corpus ON corpus.document_id = posting.document_id
            GROUP BY posting.term
        ),
        bm25_scores AS (
            SELECT posting.section_id,
                   SUM(
                       LN(1.0 + (
                           corpus.section_count - statistics.document_frequency + 0.5
                       ) / (statistics.document_frequency + 0.5))
                       * posting.term_frequency * (query_value.k1 + 1.0)
                       / (
                           posting.term_frequency + query_value.k1 * (
                               1.0 - query_value.b + query_value.b
                               * section.token_count / corpus.average_section_length
                           )
                       )
                   )::DOUBLE PRECISION AS bm25_score
            FROM ai8_script_bm25_terms posting
            JOIN query_terms query_term ON query_term.term = posting.term
            JOIN term_statistics statistics ON statistics.term = posting.term
            JOIN ai8_script_sections section ON section.id = posting.section_id
                AND section.document_id = posting.document_id
            JOIN selected_corpus corpus ON corpus.document_id = posting.document_id
            CROSS JOIN query_input query_value
            GROUP BY posting.section_id
        ),
        bm25_enriched AS (
            SELECT corpus.document_id, corpus.name, corpus.title, corpus.relative_path,
                   section.id AS section_id, section.section_order, section.heading, section.content,
                   scores.bm25_score,
                   CASE
                       WHEN LOWER(section.heading) = LOWER(query_value.raw_query) THEN 0.50
                       WHEN section.heading ILIKE query_value.like_pattern ESCAPE '\\' THEN 0.35
                       WHEN section.content ILIKE query_value.like_pattern ESCAPE '\\' THEN 0.20
                       ELSE 0.0
                   END::DOUBLE PRECISION AS exact_match_score
            FROM bm25_scores scores
            JOIN ai8_script_sections section ON section.id = scores.section_id
            JOIN selected_corpus corpus ON corpus.document_id = section.document_id
            CROSS JOIN query_input query_value
        ),
        fuzzy_enriched AS (
            SELECT corpus.document_id, corpus.name, corpus.title, corpus.relative_path,
                   section.id AS section_id, section.section_order, section.heading, section.content,
                   GREATEST(
                       similarity(section.heading, query_value.raw_query),
                       word_similarity(query_value.raw_query, section.content)
                   )::DOUBLE PRECISION AS trigram_score
            FROM selected_corpus corpus
            JOIN ai8_script_sections section ON section.document_id = corpus.document_id
            CROSS JOIN query_input query_value
            WHERE NOT EXISTS (
                SELECT 1 FROM bm25_scores scores WHERE scores.section_id = section.id
            )
              AND (SELECT COUNT(*) FROM bm25_scores) < query_value.result_limit
              AND (
                  section.heading ILIKE query_value.like_pattern ESCAPE '\\' OR
                  section.content ILIKE query_value.like_pattern ESCAPE '\\' OR
                  similarity(section.heading, query_value.raw_query) >= 0.16 OR
                  word_similarity(query_value.raw_query, section.content) >= 0.36
              )
        ),
        combined_candidates AS (
            SELECT document_id, name, title, relative_path, section_id, section_order,
                   heading, content, bm25_score, exact_match_score,
                   0.0::DOUBLE PRECISION AS trigram_score,
                   (bm25_score + exact_match_score)::DOUBLE PRECISION AS score,
                   CASE WHEN exact_match_score > 0
                       THEN ARRAY['bm25', 'exact']::TEXT[]
                       ELSE ARRAY['bm25']::TEXT[]
                   END AS retrieval_channels
            FROM bm25_enriched
            UNION ALL
            SELECT document_id, name, title, relative_path, section_id, section_order,
                   heading, content, 0.0::DOUBLE PRECISION AS bm25_score,
                   0.0::DOUBLE PRECISION AS exact_match_score, trigram_score,
                   (-1.0 + trigram_score)::DOUBLE PRECISION AS score,
                   ARRAY['trigram']::TEXT[] AS retrieval_channels
            FROM fuzzy_enriched
        )
        SELECT * FROM combined_candidates
        ORDER BY score DESC, section_order ASC
        LIMIT %s
    """

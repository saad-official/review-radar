-- 0002_embedding_dimensions: reviews.embedding and themes.embedding become
-- vector({{EMBEDDING_DIMENSIONS}}). `uv run migrate` substitutes the placeholder from
-- EMBEDDING_DIMENSIONS (default 768), the same setting that sizes the embedder's vectors
-- (docs/decisions/0005-voyage-embeddings.md: Voyage voyage-4-lite at 1024).
--
-- Vectors of another model or dimension are meaningless next to the new ones, so a column
-- whose dimension changes is retyped `USING NULL`: every stored vector is dropped. The next
-- run re-embeds the analysed reviews (an analysed review with no embedding stays queued)
-- and rebuilds the centroids of their themes. A column already at the target dimension is
-- left alone, so rendering this at the current dimension (a fresh 768-d database) keeps
-- every vector.
--
-- pgvector cannot change the dimension of an indexed column, so any index on the column
-- (HNSW/IVFFlat; 0001 creates none, exact scans are fine at this scale) is dropped first
-- and recreated from its saved definition afterwards. For pgvector, `atttypmod` is the
-- column's dimension.

do $$
declare
    target constant integer := {{EMBEDDING_DIMENSIONS}};
    col record;
    idx record;
    saved text[];
    definition text;
begin
    for col in
        select c.oid as rel, c.relname as tbl, a.atttypmod as dims
        from pg_attribute a
        join pg_class c on c.oid = a.attrelid
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'review_radar'
          and c.relname in ('reviews', 'themes')
          and a.attname = 'embedding'
          and not a.attisdropped
    loop
        continue when col.dims = target;
        saved := '{}';
        for idx in
            select i.indexrelid::regclass::text as name, pg_get_indexdef(i.indexrelid) as def
            from pg_index i
            join pg_attribute ia on ia.attrelid = i.indrelid and ia.attnum = any (i.indkey)
            where i.indrelid = col.rel and ia.attname = 'embedding'
        loop
            saved := saved || idx.def;
            execute format('drop index %s', idx.name);
        end loop;
        execute format(
            'alter table review_radar.%I alter column embedding type vector(%s) using null',
            col.tbl,
            target
        );
        foreach definition in array saved loop
            execute definition;
        end loop;
        raise notice 'review_radar.%.embedding: vector(%) -> vector(%), % index(es) rebuilt',
            col.tbl, col.dims, target, coalesce(array_length(saved, 1), 0);
    end loop;
end
$$;

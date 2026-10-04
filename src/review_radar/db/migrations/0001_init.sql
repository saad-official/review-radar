-- 0001_init: the schema from docs/spec.md section 5, plus additions the spec was silent on
-- (each marked ADDED with its reason).
--
-- Everything lives in schema `review_radar`, so this app can share a Neon project with
-- others and `DROP SCHEMA review_radar CASCADE` is a clean uninstall. pgvector provides the
-- `vector` type; on Neon it is available to every database (`create extension` is enough).

create extension if not exists vector;
create schema if not exists review_radar;

create table if not exists review_radar.apps (
    id                       uuid primary key,
    store                    text not null check (store in ('ios', 'android')),
    store_id                 text not null,
    name                     text not null,
    country                  text not null default 'us',
    github_repo              text,                -- "owner/name" for approved issues
    policy                   text not null default '',
    -- ADDED: read routes are public only for apps marked public (the demo app).
    public                   boolean not null default false,
    -- ADDED: per-app GitHub token, AES-256-GCM with an HKDF-derived per-app key
    -- (crypto.py). Never returned by the API; the plaintext never touches the database.
    github_token_ciphertext  text,
    created_at               timestamptz not null default now(),
    unique (store, store_id, country)
);

create table if not exists review_radar.reviews (
    id               uuid primary key,
    app_id           uuid not null references review_radar.apps (id) on delete cascade,
    -- The spec says `store_review_id unique`; unique per app is the correct scope (two CSV
    -- imports for different apps may reuse ids).
    store_review_id  text not null,
    source           text not null default 'appstore' check (source in ('appstore', 'csv', 'googleplay')),
    author           text,
    rating           smallint check (rating between 1 and 5),
    title            text not null default '',
    body             text not null,
    app_version      text,
    date             timestamptz,
    fetched_at       timestamptz not null default now(),
    -- gemini-embedding-001 at 768 dimensions. A corpus is tied to one embedding model.
    embedding        vector(768),
    unique (app_id, store_review_id)
);
create index if not exists reviews_app_date on review_radar.reviews (app_id, date desc nulls last);

-- Model-inferred fields, labelled with the model and prompt version that produced them.
create table if not exists review_radar.signals (
    review_id       uuid primary key references review_radar.reviews (id) on delete cascade,
    category        text not null check (category in ('bug', 'request', 'praise', 'billing', 'performance', 'other')),
    sentiment       text not null check (sentiment in ('positive', 'neutral', 'negative', 'mixed')),
    severity        smallint not null check (severity between 1 and 5),
    feature_area    text,                                 -- ADDED: "suspected area" for issues
    devices         text[] not null default '{}',
    os_versions     text[] not null default '{}',
    app_versions    text[] not null default '{}',         -- ADDED: versions *mentioned* in text
    quotes          text[] not null default '{}',
    flags           text[] not null default '{}',         -- ADDED: deterministic, e.g. prompt_injection
    model           text not null,
    prompt_version  text not null,
    created_at      timestamptz not null default now()
);

create table if not exists review_radar.themes (
    id            uuid primary key,
    app_id        uuid not null references review_radar.apps (id) on delete cascade,
    title         text not null,
    summary       text not null,
    kind          text not null check (kind in ('bug', 'request', 'praise', 'billing', 'other')),
    status        text not null default 'open' check (status in ('open', 'resolved', 'ignored')),
    review_count  integer not null default 0,
    quotes        jsonb not null default '[]'::jsonb,     -- ADDED: [{review_id, text}], verified substrings
    embedding     vector(768),                            -- normalised centroid of member reviews
    first_run_id  uuid,                                   -- ADDED: provenance
    last_run_id   uuid,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    -- ADDED: keyword half of the hybrid memory search.
    tsv           tsvector generated always as (to_tsvector('english', title || ' ' || summary)) stored
);
create index if not exists themes_app on review_radar.themes (app_id, status);
create index if not exists themes_tsv on review_radar.themes using gin (tsv);

create table if not exists review_radar.theme_reviews (
    theme_id    uuid not null references review_radar.themes (id) on delete cascade,
    review_id   uuid not null references review_radar.reviews (id) on delete cascade,
    similarity  real,                                     -- ADDED: cosine to the centroid when linked
    run_id      uuid,                                     -- ADDED: which run linked it
    primary key (theme_id, review_id)
);
create index if not exists theme_reviews_review on review_radar.theme_reviews (review_id);

create table if not exists review_radar.runs (
    id           uuid primary key,
    app_id       uuid not null references review_radar.apps (id) on delete cascade,
    status       text not null default 'queued' check (status in
                 ('queued', 'fetching', 'extracting', 'embedding', 'clustering', 'proposing', 'done', 'failed')),
    trigger      text not null default 'manual' check (trigger in ('manual', 'cron', 'demo', 'eval')),
    budget       jsonb not null default '{}'::jsonb,
    usage        jsonb,
    stats        jsonb not null default '{}'::jsonb,      -- ADDED: per-stage counts for the UI
    summary      text,
    error        text,
    -- ADDED: rate limiting and lease-based resumable processing (as in Changelog Forge).
    client_key   text,
    attempts     smallint not null default 0,
    lease_until  timestamptz,
    created_at   timestamptz not null default now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
create index if not exists runs_app_created on review_radar.runs (app_id, created_at desc);
create index if not exists runs_client_recent on review_radar.runs (client_key, created_at);

-- ADDED: per-stage checkpoints so a /process request killed at its time limit resumes.
create table if not exists review_radar.run_checkpoints (
    run_id      uuid not null references review_radar.runs (id) on delete cascade,
    key         text not null,
    data        jsonb not null,
    created_at  timestamptz not null default now(),
    primary key (run_id, key)
);

create table if not exists review_radar.proposals (
    id               uuid primary key,
    app_id           uuid not null references review_radar.apps (id) on delete cascade,
    run_id           uuid references review_radar.runs (id) on delete set null,
    kind             text not null check (kind in ('reply', 'issue')),
    theme_id         uuid references review_radar.themes (id) on delete set null,
    review_id        uuid references review_radar.reviews (id) on delete set null,
    draft            jsonb not null,
    reasoning        text not null default '',
    guardrails       jsonb not null default '{}'::jsonb,
    status           text not null default 'proposed' check (status in
                     ('proposed', 'approved', 'rejected', 'executed', 'failed')),
    decided_by       text,
    decided_at       timestamptz,
    decision_reason  text,                                -- ADDED: "declined: duplicate of #42" (memory)
    result           jsonb,                               -- issue url/number, or the error
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    check ((kind = 'reply' and review_id is not null) or (kind = 'issue' and theme_id is not null))
);
create index if not exists proposals_app_status on review_radar.proposals (app_id, status, created_at desc);
-- ADDED: memory as constraints. One reply proposal per review and one issue proposal per
-- theme, ever (a rejected one included): the agent cannot re-propose what a human decided.
create unique index if not exists proposals_one_reply on review_radar.proposals (review_id) where kind = 'reply';
create unique index if not exists proposals_one_issue on review_radar.proposals (theme_id) where kind = 'issue';

-- The trajectory: every workflow stage, model turn, tool call and tool result. Append-only.
create table if not exists review_radar.run_steps (
    run_id  uuid not null references review_radar.runs (id) on delete cascade,
    seq     integer not null,
    at      timestamptz not null default now(),
    stage   text not null,                                -- ADDED: fetch|extract|embed|cluster|propose|finish
    kind    text not null check (kind in ('tool_call', 'tool_result', 'model', 'note')),
    name    text not null,
    args    jsonb not null default '{}'::jsonb,
    result  jsonb not null default '{}'::jsonb,
    usage   jsonb,
    primary key (run_id, seq)
);

-- Append-only audit, as in the other apps of the series.
create table if not exists review_radar.agent_events (
    id              uuid primary key,
    app_id          uuid references review_radar.apps (id) on delete cascade,
    actor           text not null check (actor in ('agent', 'operator', 'system', 'cron')),
    type            text not null,
    entity_type     text,
    entity_id       uuid,
    input           jsonb,
    output          jsonb,
    model           text,
    prompt_version  text,
    tokens_in       integer,
    tokens_out      integer,
    latency_ms      integer,
    created_at      timestamptz not null default now()
);
create index if not exists agent_events_app_created on review_radar.agent_events (app_id, created_at desc);

-- Append-only enforcement: UPDATE and DELETE raise. Rows still go away with their parent
-- (ON DELETE CASCADE from runs/apps) because the trigger allows deletes that cascade from
-- a parent row being removed (pg_trigger_depth() > 0 inside the cascade).
create or replace function review_radar.forbid_mutation() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' and pg_trigger_depth() > 1 then
        return old;  -- cascading delete from the parent run or app
    end if;
    raise exception '% is append-only (% refused)', tg_table_name, tg_op
        using errcode = 'insufficient_privilege';
end;
$$;

drop trigger if exists run_steps_append_only on review_radar.run_steps;
create trigger run_steps_append_only before update or delete on review_radar.run_steps
    for each row execute function review_radar.forbid_mutation();

drop trigger if exists agent_events_append_only on review_radar.agent_events;
create trigger agent_events_append_only before update or delete on review_radar.agent_events
    for each row execute function review_radar.forbid_mutation();

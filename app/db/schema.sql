-- painbot schema. Idempotent: every statement is IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,              -- hn | hn_vintage | reddit | github
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_cursor TEXT,
    last_run_at INTEGER,
    last_error  TEXT,
    UNIQUE(kind, name)
);

CREATE TABLE IF NOT EXISTS raw_items (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    ext_id      TEXT NOT NULL,
    url         TEXT,
    title       TEXT,
    body        TEXT,
    author      TEXT,
    score       INTEGER DEFAULT 0,
    comments    INTEGER DEFAULT 0,
    created_utc INTEGER,
    fetched_at  INTEGER NOT NULL,
    simhash     TEXT,
    state       TEXT NOT NULL DEFAULT 'new', -- new | screened | duplicate | rejected
    reject_reason TEXT,
    raw_json    TEXT,
    UNIQUE(source_id, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_state ON raw_items(state);
CREATE INDEX IF NOT EXISTS idx_raw_simhash ON raw_items(simhash);

CREATE TABLE IF NOT EXISTS pains (
    id                INTEGER PRIMARY KEY,
    raw_item_id       INTEGER NOT NULL REFERENCES raw_items(id) ON DELETE CASCADE,
    title_ru          TEXT NOT NULL,
    summary           TEXT,
    audience          TEXT,
    evidence_quote    TEXT,
    is_it             INTEGER NOT NULL DEFAULT 1,   -- hard IT-product gate
    era               TEXT NOT NULL DEFAULT 'fresh',-- fresh | vintage
    why_now           TEXT,                         -- what changed since; the vintage payoff
    severity          INTEGER,
    willingness_to_pay INTEGER,
    solo_feasibility  INTEGER,
    saturation        INTEGER,
    score             INTEGER,
    tags_json         TEXT DEFAULT '[]',
    cluster_id        INTEGER,
    screened_by       TEXT,
    state             TEXT NOT NULL DEFAULT 'inbox', -- inbox | approved | rejected
    created_at        INTEGER NOT NULL,
    UNIQUE(raw_item_id)
);
CREATE INDEX IF NOT EXISTS idx_pains_state_score ON pains(state, score DESC);

CREATE TABLE IF NOT EXISTS ideas (
    id                INTEGER PRIMARY KEY,
    pain_id           INTEGER NOT NULL REFERENCES pains(id) ON DELETE CASCADE,
    variant_no        INTEGER NOT NULL DEFAULT 1,
    name              TEXT NOT NULL,
    one_liner         TEXT,
    mvp_scope         TEXT,
    stack_json        TEXT DEFAULT '[]',
    integrations_json TEXT DEFAULT '[]',
    db_sketch         TEXT,
    effort_hours      INTEGER,
    cut_list          TEXT,
    moat_note         TEXT,
    created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ideas_pain ON ideas(pain_id);

CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    idea_id    INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,              -- approved | rejected
    note       TEXT,
    decided_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id             INTEGER PRIMARY KEY,
    idea_id        INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    md_text        TEXT,
    tg_html        TEXT,
    channel_msg_id INTEGER,
    published_at   INTEGER,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS scripts (
    id           INTEGER PRIMARY KEY,
    idea_id      INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    hook         TEXT,
    beats_json   TEXT NOT NULL DEFAULT '[]',
    vo_text      TEXT,
    duration_est REAL,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id         INTEGER PRIMARY KEY,
    script_id  INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    beat_idx   INTEGER NOT NULL,
    kind       TEXT NOT NULL,              -- ui | diagram | photo
    spec       TEXT,                       -- html / mermaid / t2i prompt
    provider   TEXT,
    model      TEXT,
    local_path TEXT,
    r2_key     TEXT,
    public_url TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    cost_usd   REAL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(script_id, beat_idx)
);

CREATE TABLE IF NOT EXISTS renders (
    id          INTEGER PRIMARY KEY,
    script_id   INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    local_path  TEXT,
    r2_key      TEXT,
    public_url  TEXT,
    size_bytes  INTEGER,
    duration    REAL,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    tg_file_id  TEXT,
    uploaded_at INTEGER,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | failed
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    run_after    INTEGER,
    chat_id      INTEGER,
    message_id   INTEGER,
    created_at   INTEGER NOT NULL,
    started_at   INTEGER,
    finished_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, run_after);

CREATE TABLE IF NOT EXISTS costs (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER,
    provider   TEXT,
    model      TEXT,
    tok_in     INTEGER DEFAULT 0,
    tok_out    INTEGER DEFAULT 0,
    usd        REAL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS article_assets (
    id         INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    block_idx  INTEGER NOT NULL,
    kind       TEXT NOT NULL,              -- mockup | diagram
    brief      TEXT,
    spec       TEXT,                       -- mermaid source or html fragment
    caption    TEXT,
    path       TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    UNIQUE(article_id, block_idx)
);

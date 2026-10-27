-- MLLM verifier audit store. One SQLite file is the single source of truth;
-- metrics are computed on read, never stored.

PRAGMA foreign_keys = ON;

-- Exact rendered prompt text; id is the first 12 hex digits of its sha256.
CREATE TABLE IF NOT EXISTS prompt_version (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    text        TEXT NOT NULL,
    parent_id   TEXT REFERENCES prompt_version(id),
    note        TEXT,
    created_at  TEXT NOT NULL
);

-- Any candidate crop that was shown to a model, as a query or as a demo.
-- inputs: {"t1": path, "t2": path, "context_t1": path, ...} on the server disk.
CREATE TABLE IF NOT EXISTS sample (
    id          INTEGER PRIMARY KEY,
    dataset     TEXT NOT NULL,
    split       TEXT NOT NULL,
    cid         TEXT NOT NULL,
    image       TEXT,
    source      TEXT,
    label       INTEGER,
    inputs      TEXT NOT NULL,
    meta        TEXT,
    UNIQUE (dataset, split, cid)
);

-- mode: zero | few | many | batched_many. id is a hash of the ordered items.
CREATE TABLE IF NOT EXISTS demo_set (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    mode        TEXT NOT NULL CHECK (mode IN ('zero', 'few', 'many', 'batched_many')),
    k           INTEGER NOT NULL,
    strategy    TEXT,
    seed        INTEGER,
    note        TEXT,
    created_at  TEXT NOT NULL
);

-- shown_label is the answer text the prompt gives for this demo ("yes"/"no").
CREATE TABLE IF NOT EXISTS demo_set_item (
    demo_set_id TEXT NOT NULL REFERENCES demo_set(id),
    position    INTEGER NOT NULL,
    sample_id   INTEGER NOT NULL REFERENCES sample(id),
    shown_label TEXT,
    PRIMARY KEY (demo_set_id, position)
);

-- template: the chat messages as sent, with images as refs such as
-- {"type": "image", "ref": "demo[3].t2"} or {"type": "image", "ref": "query[0].t1"}.
CREATE TABLE IF NOT EXISTS run (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    experiment  TEXT,
    model       TEXT NOT NULL,
    prompt_id   TEXT NOT NULL REFERENCES prompt_version(id),
    demo_set_id TEXT NOT NULL REFERENCES demo_set(id),
    readout     TEXT,
    view        TEXT,
    packing     TEXT,
    batch_size  INTEGER NOT NULL DEFAULT 1,
    template    TEXT NOT NULL,
    git_commit  TEXT,
    config      TEXT,
    created_at  TEXT NOT NULL
);

-- One model call. Only needed when one call answers several queries
-- (batched ICL); its raw text is stored once here.
CREATE TABLE IF NOT EXISTS call (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES run(id),
    idx         INTEGER NOT NULL,
    raw_text    TEXT,
    meta        TEXT,
    UNIQUE (run_id, idx)
);

CREATE TABLE IF NOT EXISTS output (
    run_id      INTEGER NOT NULL REFERENCES run(id),
    sample_id   INTEGER NOT NULL REFERENCES sample(id),
    call_id     INTEGER REFERENCES call(id),
    position    INTEGER,
    p_yes       REAL,
    logit_diff  REAL,
    yes_no_mass REAL,
    answer      TEXT,
    confidence  INTEGER,
    raw_text    TEXT,
    extra       TEXT,
    PRIMARY KEY (run_id, sample_id)
);

CREATE INDEX IF NOT EXISTS output_sample ON output(sample_id);
CREATE INDEX IF NOT EXISTS item_sample ON demo_set_item(sample_id);

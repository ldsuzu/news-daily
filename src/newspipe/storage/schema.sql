-- 每日简报 · SQLite schema
-- 原则：历史永久保留，不做滚动过期；正文落库，离线可读。

CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    domain        TEXT NOT NULL,
    type          TEXT NOT NULL,
    url           TEXT NOT NULL,
    weight        REAL NOT NULL DEFAULT 1.0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    needs_proxy   INTEGER NOT NULL DEFAULT 0,
    extract       INTEGER NOT NULL DEFAULT 1,   -- 该源的文章页能否抽出正文（SPA 站点抽不出）
    last_ok_at    TEXT,
    last_error    TEXT,
    fail_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    url_hash      TEXT NOT NULL UNIQUE,
    source_id     TEXT NOT NULL,
    domain        TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    title_en      TEXT NOT NULL DEFAULT '',
    title_key     TEXT NOT NULL DEFAULT '',
    published_at  TEXT,
    pub_date      TEXT NOT NULL DEFAULT '',   -- 本地时区的 YYYY-MM-DD，按天查/归档都用它
    fetched_at    TEXT NOT NULL,
    lang          TEXT NOT NULL DEFAULT '',
    excerpt       TEXT NOT NULL DEFAULT '',
    content_text  TEXT NOT NULL DEFAULT '',
    summary_cn    TEXT NOT NULL DEFAULT '',
    title_cn      TEXT NOT NULL DEFAULT '',   -- LLM 翻译/精简后的中文标题
    points_json   TEXT NOT NULL DEFAULT '[]',
    score         REAL NOT NULL DEFAULT 0,    -- 规则分（源权重 + 时效 + 关键词）
    llm_score     REAL,                       -- LLM 给的热度分 0-10；有就优先用它排序
    llm_at        TEXT,                       -- 什么时候被 LLM 处理过（NULL = 还没处理）
    cluster_id    INTEGER,
    cluster_size  INTEGER NOT NULL DEFAULT 1,
    origin        TEXT NOT NULL DEFAULT 'local',   -- local | remote（海外分身）
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_pubdate   ON items(pub_date DESC);
CREATE INDEX IF NOT EXISTS idx_items_domain    ON items(domain);
CREATE INDEX IF NOT EXISTS idx_items_source    ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_items_score     ON items(score DESC);
CREATE INDEX IF NOT EXISTS idx_items_titlekey  ON items(title_key);
CREATE INDEX IF NOT EXISTS idx_items_cluster   ON items(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
    id                     INTEGER PRIMARY KEY,
    cluster_key            TEXT NOT NULL UNIQUE,
    representative_item_id INTEGER NOT NULL,
    size                   INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digests (
    id         INTEGER PRIMARY KEY,
    date       TEXT NOT NULL,
    domain     TEXT NOT NULL,
    item_ids   TEXT NOT NULL DEFAULT '[]',
    markdown   TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(date, domain)
);

CREATE TABLE IF NOT EXISTS read_state (
    item_id  INTEGER PRIMARY KEY,
    read_at  TEXT,
    starred  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id         INTEGER PRIMARY KEY,
    source_id  TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    error      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_fetch_log_source ON fetch_log(source_id, started_at DESC);

-- LLM 用量账本：每次调用记一笔，界面据此把 token 消耗换算成人民币
CREATE TABLE IF NOT EXISTS llm_usage (
    id            INTEGER PRIMARY KEY,
    ts            TEXT NOT NULL,
    model         TEXT NOT NULL,
    items_count   INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,   -- 未命中缓存的输入
    cached_tokens INTEGER NOT NULL DEFAULT 0,   -- 命中缓存的输入（便宜得多）
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cny      REAL NOT NULL DEFAULT 0,      -- 按配置单价换算出来的人民币
    ok            INTEGER NOT NULL DEFAULT 1,
    note          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts DESC);

-- @@SPLIT@@
-- 以上是核心表。下面这段是全文检索：单独执行，失败也不影响其它功能。
-- 全历史全文检索（技术框架 §4.2）。中文用 trigram 分词；老 SQLite 降级为 unicode61。
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, title_en, summary_cn, content_text,
    content='items',
    content_rowid='id',
    tokenize='__TOKENIZER__'
);

CREATE TRIGGER IF NOT EXISTS items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, title_en, summary_cn, content_text)
    VALUES (new.id, new.title, new.title_en, new.summary_cn, new.content_text);
END;

CREATE TRIGGER IF NOT EXISTS items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, title_en, summary_cn, content_text)
    VALUES ('delete', old.id, old.title, old.title_en, old.summary_cn, old.content_text);
END;

CREATE TRIGGER IF NOT EXISTS items_fts_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, title_en, summary_cn, content_text)
    VALUES ('delete', old.id, old.title, old.title_en, old.summary_cn, old.content_text);
    INSERT INTO items_fts(rowid, title, title_en, summary_cn, content_text)
    VALUES (new.id, new.title, new.title_en, new.summary_cn, new.content_text);
END;

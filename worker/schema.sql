CREATE TABLE IF NOT EXISTS subscribers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  verified INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 0,
  token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  verified_at TEXT,
  unsubscribed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subscribers_active ON subscribers(active, verified);
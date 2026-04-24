# Schema

```sql
CREATE TABLE memory_documents (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  updated_at DATETIME NOT NULL,
  deleted_at DATETIME,
  sensitive INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE vec_memory_chunks USING vec0(
  chunk_id TEXT PRIMARY KEY,
  embedding FLOAT[384]
);
```

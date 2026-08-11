-- Record and enforce one saved conversation per chat session without modifying
-- conversation data. The target deployment already ran its one-off cleanup and
-- created this index. On any other database that still contains duplicates,
-- index creation must fail visibly rather than deleting rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_conversations_session_id_unique
  ON chat_conversations (session_id);

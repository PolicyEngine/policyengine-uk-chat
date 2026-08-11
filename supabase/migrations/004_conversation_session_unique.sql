-- One saved conversation per chat session. Preserve the newest transcript,
-- earliest creation timestamp, and any existing share token while cleaning up
-- legacy duplicates created by racing frontend saves.
WITH canonical AS (
  SELECT DISTINCT ON (session_id)
    id,
    session_id
  FROM chat_conversations
  ORDER BY session_id, updated_at DESC, id DESC
),
preserved AS (
  SELECT
    session_id,
    min(created_at) AS created_at,
    (array_agg(share_token ORDER BY updated_at DESC, id DESC)
      FILTER (WHERE share_token IS NOT NULL))[1] AS share_token
  FROM chat_conversations
  GROUP BY session_id
)
UPDATE chat_conversations AS conversation
SET
  created_at = preserved.created_at,
  share_token = coalesce(conversation.share_token, preserved.share_token)
FROM canonical, preserved
WHERE conversation.id = canonical.id
  AND conversation.session_id = preserved.session_id;

WITH canonical AS (
  SELECT DISTINCT ON (session_id)
    id,
    session_id
  FROM chat_conversations
  ORDER BY session_id, updated_at DESC, id DESC
)
DELETE FROM chat_conversations AS duplicate
USING canonical
WHERE duplicate.session_id = canonical.session_id
  AND duplicate.id <> canonical.id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_conversations_session_id_unique
  ON chat_conversations (session_id);

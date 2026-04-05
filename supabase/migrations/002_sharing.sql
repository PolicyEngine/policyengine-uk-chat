-- Add sharing and author columns to chat_conversations
ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS share_token TEXT;
ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS user_email TEXT;
CREATE INDEX IF NOT EXISTS idx_chat_conversations_share_token ON chat_conversations (share_token);

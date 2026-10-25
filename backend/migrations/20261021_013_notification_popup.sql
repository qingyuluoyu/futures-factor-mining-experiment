ALTER TABLE user_notify_settings ADD COLUMN IF NOT EXISTS notify_popup_enabled BOOLEAN NOT NULL DEFAULT TRUE;

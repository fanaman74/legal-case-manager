ALTER TABLE files ADD COLUMN ocr_state TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN ocr_error TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN notes TEXT NOT NULL DEFAULT '[]';
    UPDATE files SET ocr_state = 'done' WHERE ocr = 1 AND scanned_pages = '[]';

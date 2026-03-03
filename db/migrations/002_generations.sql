-- 内容生成任务表（视频/图片/文字），用于 SeedDance 与 LLM 生成及轮询
CREATE TABLE IF NOT EXISTS generations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  shopify_store_id VARCHAR(64) NOT NULL,
  type VARCHAR(16) NOT NULL COMMENT 'video | image | text',
  status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending | processing | completed | failed',
  prompt_used TEXT NOT NULL,
  trend_snapshot JSON NULL,
  brand_snapshot JSON NULL,
  external_id VARCHAR(128) NULL COMMENT 'e.g. SeedDance video_id',
  result_url TEXT NULL,
  result_text TEXT NULL,
  error_message TEXT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_generations_store_id (shopify_store_id),
  INDEX idx_generations_type (type),
  INDEX idx_generations_status (status),
  INDEX idx_generations_external_id (external_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 内容生成任务表（视频/图片/文字），用于 SeedDance 与 LLM 生成及轮询
CREATE TABLE IF NOT EXISTS generations (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  shopify_store_id VARCHAR(64) NOT NULL COMMENT '所属店铺 ID，多租户隔离',
  type VARCHAR(16) NOT NULL COMMENT '任务类型：video | text',
  status VARCHAR(32) NOT NULL DEFAULT 'queued' COMMENT '状态：queued | running | succeeded | failed ｜ expired ',
  thread_id VARCHAR(64) NULL COMMENT '所属视频生成会话 ID（仅视频 thread 任务有值）',
  prompt_used TEXT NOT NULL COMMENT '实际发给模型/API 的 prompt',
  trend_snapshot JSON NULL COMMENT '发起任务时的热点快照（TrendObject）',
  brand_snapshot JSON NULL COMMENT '发起任务时的品牌快照（BrandObject）',
  external_id VARCHAR(128) NULL COMMENT '第三方任务 ID，如 SeedDance video_id',
  result_url TEXT NULL COMMENT '结果资源 URL（视频）',
  result_text TEXT NULL COMMENT '文字生成结果',
  error_message TEXT NULL COMMENT '失败时的错误信息',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  INDEX idx_generations_store_id (shopify_store_id),
  INDEX idx_generations_type (type),
  INDEX idx_generations_status (status),
  INDEX idx_generations_thread_id (thread_id),
  INDEX idx_generations_external_id (external_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容生成任务表';

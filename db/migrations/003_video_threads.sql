-- 视频会话索引表：用于历史会话列表查询与归属校验（不存完整 LangGraph state）
CREATE TABLE IF NOT EXISTS video_threads (
  thread_id VARCHAR(64) PRIMARY KEY COMMENT 'LangGraph thread_id（UUID 字符串）',
  shopify_store_id VARCHAR(64) NOT NULL COMMENT '所属店铺 ID，多租户隔离',
  status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT '生命周期状态：running | waiting_human | finished | error',
  current_step VARCHAR(64) NULL COMMENT '当前步骤标识（如 plan_script_done / waiting_human）',
  title VARCHAR(255) NULL COMMENT '会话标题（通常取 user_input 前 100 字符）',
  user_input TEXT NULL COMMENT '用户原始输入，用于历史回放兜底',
  thumbnail_url TEXT NULL COMMENT '列表页缩略图 URL',
  revision_count INT NOT NULL DEFAULT 0 COMMENT '剧本修改轮次',
  error_message TEXT NULL COMMENT '失败原因（若有）',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  completed_at TIMESTAMP NULL DEFAULT NULL COMMENT '完成时间（finished / error 时写入）',
  INDEX idx_video_threads_store_id (shopify_store_id),
  INDEX idx_video_threads_status (status),
  INDEX idx_video_threads_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='视频会话索引表';

-- 数据库：Shopify 运营平台（市场洞察、动态调价、内容生成）
CREATE DATABASE IF NOT EXISTS `shopify_ai`;

USE `shopify_ai`;

-- ---------------------------------------------------------------------------
-- 商家表：平台注册的 Shopify 商户，与 Shopify 店铺一一对应
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchants (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  shopify_store_id VARCHAR(64) NOT NULL UNIQUE COMMENT 'Shopify 店铺数字 ID（来自 shop.json），系统内店铺唯一标识，用于 JWT sub 与多租户隔离',
  shopify_domain VARCHAR(255) NULL COMMENT '店铺域名，如 mystore.myshopify.com，用于拼 OAuth/API 请求 URL',
  shopify_category VARCHAR(128) NULL COMMENT '店铺类目（来自 Shopify shop.json，若有）',
  name VARCHAR(128) NOT NULL COMMENT '商户显示名称，亦作为登录用户名',
  email VARCHAR(128) NOT NULL UNIQUE COMMENT '登录邮箱，唯一',
  password_hash VARCHAR(256) NOT NULL COMMENT '登录密码 bcrypt 哈希',
  shopify_access_token VARCHAR(512) NULL COMMENT 'Shopify OAuth 换取的 access_token，用于代表该店铺调用 Shopify API（如发布内容、读产品）',
  is_active BOOLEAN DEFAULT TRUE COMMENT '是否启用，禁用后无法登录',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  INDEX idx_merchants_store_id (shopify_store_id),
  INDEX idx_merchants_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商家表：平台注册的 Shopify 商户';

-- ---------------------------------------------------------------------------
-- 热点表：全局热点（如 YouTube 趋势），所有商家看到同一份数据，不按店铺隔离
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hotspots (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  title VARCHAR(255) NOT NULL COMMENT '热点标题',
  summary TEXT NOT NULL COMMENT '热点摘要/描述',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  INDEX idx_hotspots_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='热点表：全局热点，所有商家共享';

-- ---------------------------------------------------------------------------
-- 品牌表：商户配置的品牌信息，与 merchants 一对多（按 merchant_id 关联）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brand (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  shopify_store_id VARCHAR(64) NOT NULL COMMENT '所属店铺 ID，便于按店铺查询',
  merchant_id INT NOT NULL COMMENT '所属商户 id，外键关联 merchants.id',
  name VARCHAR(64) NOT NULL COMMENT '品牌名称',
  core_value VARCHAR(64) NOT NULL COMMENT '品牌核心价值/目标',
  mainly_sold_products VARCHAR(64) NOT NULL COMMENT '品牌所属行业',
  tone VARCHAR(64) NOT NULL COMMENT '品牌调性',
  audience VARCHAR(64) NULL COMMENT '品牌目标受众',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  INDEX idx_brand_store_id (shopify_store_id),
  INDEX idx_brand_merchant_id (merchant_id),
  INDEX idx_brand_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='品牌表：商户配置的品牌信息';

-- ---------------------------------------------------------------------------
-- 热点推荐邮件定时：每商户一条配置，含最低契合度与发送节奏
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchant_hotspot_recommend_email_schedule (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  merchant_id INT NOT NULL UNIQUE COMMENT '商户 id，外键 merchants.id，每商户至多一条定时配置',
  is_enabled BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否启用定时发送热点推荐邮件',
  mode VARCHAR(20) NOT NULL DEFAULT 'interval_from_now' COMMENT '定时模式：interval_from_now|daily_fixed|interval_from_fixed',
  min_compatibility_score DECIMAL(5,2) NOT NULL DEFAULT 40.00 COMMENT '定时任务使用的最低契合度阈值，与 /hotspot/recommend 语义一致，范围建议 0–100',
  interval_hours INT UNSIGNED NOT NULL DEFAULT 24 COMMENT '两次发送之间的最短间隔（小时），用于防重复',
  timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai' COMMENT '解释 send_hour/send_minute 的 IANA 时区名，如 Asia/Shanghai、UTC',
  send_hour TINYINT UNSIGNED NOT NULL DEFAULT 9 COMMENT '在 timezone 下每日触发的整点小时 0–23',
  send_minute TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '在 timezone 下每日触发的分钟 0–59',
  last_sent_at TIMESTAMP NULL DEFAULT NULL COMMENT '上次成功触发发送的时间（由业务在发送成功后更新）',
  last_triggered_at TIMESTAMP NULL DEFAULT NULL COMMENT '上次尝试触发时间（无论发送成功与否）',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  CONSTRAINT fk_mhrees_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id) ON DELETE CASCADE,
  INDEX idx_mhrees_enabled (is_enabled),
  INDEX idx_mhrees_merchant_id (merchant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商户热点推荐邮件定时：契合度阈值、发送间隔与每日时刻';

-- ---------------------------------------------------------------------------
-- 热点推荐邮件发送记录：记录已发送热点，用于去重，避免重复通知同一商户
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchant_hotspot_recommend_email_delivery (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  merchant_id INT NOT NULL COMMENT '商户 id，外键 merchants.id',
  schedule_id INT NULL COMMENT '来源定时配置 id，允许为空以兼容手动触发',
  platform VARCHAR(32) NOT NULL DEFAULT 'youtube' COMMENT '热点平台',
  trend_id VARCHAR(128) NOT NULL COMMENT '热点原始 id',
  brand_fp VARCHAR(32) NOT NULL COMMENT '品牌指纹',
  trend_fp VARCHAR(32) NOT NULL COMMENT '热点指纹',
  compatibility_score DECIMAL(5,2) NOT NULL COMMENT '发送时匹配分',
  min_score_at_send DECIMAL(5,2) NOT NULL COMMENT '发送时阈值',
  matched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '匹配完成时间',
  sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '邮件发送时间',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  CONSTRAINT fk_mhred_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id) ON DELETE CASCADE,
  CONSTRAINT fk_mhred_schedule FOREIGN KEY (schedule_id) REFERENCES merchant_hotspot_recommend_email_schedule (id) ON DELETE SET NULL,
  UNIQUE KEY uq_mhred_merchant_brand_trend (merchant_id, brand_fp, trend_fp),
  INDEX idx_mhred_merchant_sent_at (merchant_id, sent_at),
  INDEX idx_mhred_schedule_id (schedule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商户热点推荐邮件已发送记录';

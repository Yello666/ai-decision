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
  industry VARCHAR(64) NOT NULL COMMENT '品牌所属行业',
  tone VARCHAR(64) NOT NULL COMMENT '品牌调性',
  audience VARCHAR(64) NULL COMMENT '品牌目标受众',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  INDEX idx_brand_store_id (shopify_store_id),
  INDEX idx_brand_merchant_id (merchant_id),
  INDEX idx_brand_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='品牌表：商户配置的品牌信息';

CREATE DATABASE IF NOT EXISTS `shopify_ai`;

USE `shopify_ai`;

CREATE TABLE IF NOT EXISTS merchants (
  id INT AUTO_INCREMENT PRIMARY KEY,
  shopify_store_id VARCHAR(64) NOT NULL UNIQUE,
  shopify_domain VARCHAR(255),
  shopify_category VARCHAR(128),
  name VARCHAR(128) NOT NULL,
  email VARCHAR(128) NOT NULL UNIQUE,
  password_hash VARCHAR(256) NOT NULL,
  brand_tone VARCHAR(255),
  preferences TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hotspots (
  id INT AUTO_INCREMENT PRIMARY KEY,
  shopify_store_id VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  summary TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_hotspots_store_id (shopify_store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS contents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  shopify_store_id VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  prompt TEXT NOT NULL,
  generated_text TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_contents_store_id (shopify_store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 品牌表，与 merchants 一对一/多（按 merchant_id 关联）
CREATE TABLE IF NOT EXISTS brand (
  id INT AUTO_INCREMENT PRIMARY KEY,
  shopify_store_id VARCHAR(64) NOT NULL,
  merchant_id INT NOT NULL,
  name VARCHAR(64) NOT NULL COMMENT '品牌名称',
  core_value VARCHAR(64) NOT NULL COMMENT '品牌核心价值，目标',
  industry VARCHAR(64) NOT NULL COMMENT '品牌所属行业',
  tone VARCHAR(64) NOT NULL COMMENT '品牌调性',
  audience VARCHAR(64) NULL COMMENT '品牌目标受众',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_brand_store_id (shopify_store_id),
  INDEX idx_brand_merchant_id (merchant_id),
  INDEX idx_brand_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

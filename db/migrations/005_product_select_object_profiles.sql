-- ---------------------------------------------------------------------------
-- Product Select：商品机会规划/预测（FBA 前置）
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS product_select_object_profiles (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  object_id INT NOT NULL COMMENT '对应识图物件/商品机会 id',

  cost_price_min DECIMAL(12,2) NULL COMMENT '预测采购成本下限',
  cost_price_max DECIMAL(12,2) NULL COMMENT '预测采购成本上限',
  selling_price_min DECIMAL(12,2) NULL COMMENT '预测售价下限',
  selling_price_max DECIMAL(12,2) NULL COMMENT '预测售价上限',
  currency VARCHAR(16) NULL DEFAULT 'USD' COMMENT '价格币种',

  length_cm DECIMAL(10,2) NULL COMMENT '长（cm）',
  width_cm DECIMAL(10,2) NULL COMMENT '宽（cm）',
  height_cm DECIMAL(10,2) NULL COMMENT '高（cm）',
  volume_cm3 DECIMAL(12,2) NULL COMMENT '体积（cm³），不规则件可单独填写',
  weight_value DECIMAL(10,3) NULL COMMENT '重量数值',
  weight_unit VARCHAR(8) NULL COMMENT '重量单位：g|kg|lb|oz',

  source VARCHAR(32) NOT NULL DEFAULT 'ai' COMMENT '预测来源：ai|match|manual',
  status VARCHAR(32) NOT NULL DEFAULT 'draft' COMMENT 'draft|confirmed',
  reference_match_id INT NULL COMMENT '参考的相似商品 match id',
  notes TEXT NULL COMMENT '预测依据/备注',

  is_active BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否当前生效版本',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  CONSTRAINT fk_psop_object FOREIGN KEY (object_id) REFERENCES product_select_objects (id) ON DELETE CASCADE,
  CONSTRAINT fk_psop_match FOREIGN KEY (reference_match_id) REFERENCES product_select_matches (id) ON DELETE SET NULL,
  INDEX idx_psop_object (object_id),
  INDEX idx_psop_object_active (object_id, is_active),
  INDEX idx_psop_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product Select 商品机会规划/预测（采购售价区间、尺寸重量）';

-- ---------------------------------------------------------------------------
-- Product Select：名人/IP 内容驱动的选品发现与供应链匹配
-- ---------------------------------------------------------------------------

-- 监控对象：Instagram 名人账号、YouTube 频道、未来也可扩展关键词/IP
CREATE TABLE IF NOT EXISTS product_select_monitors (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  merchant_id INT NULL COMMENT '所属商户 id；为空表示平台级/本地实验监控对象',
  platform VARCHAR(32) NOT NULL COMMENT '平台：instagram|youtube|tiktok|manual 等',
  handle VARCHAR(255) NOT NULL COMMENT '账号/频道/关键词原始值，如 csgoniko、@MrBeast',
  display_name VARCHAR(255) NULL COMMENT '可读名称，如 NiKo（CS 电竞选手）',
  score DECIMAL(4,2) NOT NULL DEFAULT 5.00 COMMENT '监控对象评分，默认 5 分，用于排序或优先级',
  monitor_type VARCHAR(32) NOT NULL DEFAULT 'profile' COMMENT 'profile|channel|keyword|hashtag',
  is_enabled BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否启用监控',
  last_checked_at TIMESTAMP NULL DEFAULT NULL COMMENT '上次检查时间',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  CONSTRAINT fk_psm_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id) ON DELETE CASCADE,
  UNIQUE KEY uk_psm_scope (merchant_id, platform, handle, monitor_type),
  INDEX idx_psm_platform_enabled (platform, is_enabled),
  INDEX idx_psm_merchant (merchant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product Select 监控对象/监控池';

-- 采集内容：一条 Instagram 帖子或 YouTube 视频
CREATE TABLE IF NOT EXISTS product_select_contents (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  monitor_id INT NULL COMMENT '来源监控对象 id；手动导入或历史数据可为空',
  merchant_id INT NULL COMMENT '所属商户 id；为空表示平台级/本地实验数据',
  platform VARCHAR(32) NOT NULL COMMENT '平台：instagram|youtube 等',
  external_id VARCHAR(255) NOT NULL COMMENT '外部内容 id：IG post_id / YouTube video_id',
  url TEXT NULL COMMENT '帖子/视频链接',
  caption_or_title TEXT NULL COMMENT 'caption 或 title',
  published_at TIMESTAMP NULL DEFAULT NULL COMMENT '内容发布时间',
  raw_path TEXT NULL COMMENT '原始抓取/识图 JSON 文件路径（大 JSON 不直接塞主表）',
  status VARCHAR(32) NOT NULL DEFAULT 'fetched' COMMENT 'fetched|recognized|matched|failed',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  CONSTRAINT fk_psc_monitor FOREIGN KEY (monitor_id) REFERENCES product_select_monitors (id) ON DELETE SET NULL,
  CONSTRAINT fk_psc_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id) ON DELETE CASCADE,
  UNIQUE KEY uk_psc_platform_external (platform, external_id),
  INDEX idx_psc_monitor (monitor_id),
  INDEX idx_psc_merchant (merchant_id),
  INDEX idx_psc_status (status),
  INDEX idx_psc_published_at (published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product Select 采集内容（帖子/视频）';

-- 图片资产：原图、YouTube 帧、裁剪商品图
CREATE TABLE IF NOT EXISTS product_select_images (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  content_id INT NULL COMMENT '所属内容 id；单独测试图可为空',
  image_type VARCHAR(32) NOT NULL COMMENT 'source|frame|crop',
  local_path TEXT NULL COMMENT '本地文件路径',
  oss_key TEXT NULL COMMENT 'OSS object key；长期保存用，访问时重新签名',
  oss_url TEXT NULL COMMENT '临时签名 URL（可能过期，仅作调试/快照）',
  source_url TEXT NULL COMMENT '原始图片 URL（如 IG CDN）',
  width INT NULL COMMENT '图片宽度',
  height INT NULL COMMENT '图片高度',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  CONSTRAINT fk_psi_content FOREIGN KEY (content_id) REFERENCES product_select_contents (id) ON DELETE CASCADE,
  INDEX idx_psi_content (content_id),
  INDEX idx_psi_type (image_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product Select 图片资产（原图/帧/裁剪图）';

-- 识图物件：qwen-vl 识别出的可选品商品机会
CREATE TABLE IF NOT EXISTS product_select_objects (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  content_id INT NULL COMMENT '来源内容 id；单独测试图可为空',
  source_image_id INT NULL COMMENT '来源图片 id',
  crop_image_id INT NULL COMMENT '裁剪图 id',
  category VARCHAR(128) NOT NULL COMMENT '品类，如 球衣/耳机/卫衣',
  related_ip VARCHAR(255) NULL COMMENT '关联名人/IP；无法确认可为 未知',
  description TEXT NULL COMMENT '物件外观描述',
  attributes_json JSON NULL COMMENT '属性标签数组',
  ecommerce_potential VARCHAR(16) NOT NULL DEFAULT 'medium' COMMENT 'high|medium|low',
  reason TEXT NULL COMMENT '模型判断理由',
  bbox_json JSON NULL COMMENT '归一化 bbox：[x1,y1,x2,y2]',
  recognition_version INT NOT NULL DEFAULT 1 COMMENT '同一内容识别版本；force/重跑时递增',
  is_active BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否当前生效版本；历史版本保留但默认不展示',
  opportunity_score DECIMAL(6,2) NULL COMMENT '商品机会综合评分 0-100',
  opportunity_score_level VARCHAR(32) NULL COMMENT '商品机会评分等级：high|medium|low',
  opportunity_score_reason TEXT NULL COMMENT '商品机会评分原因',
  token_usage_json JSON NULL COMMENT '识图 token 用量快照',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  CONSTRAINT fk_pso_content FOREIGN KEY (content_id) REFERENCES product_select_contents (id) ON DELETE CASCADE,
  CONSTRAINT fk_pso_source_image FOREIGN KEY (source_image_id) REFERENCES product_select_images (id) ON DELETE SET NULL,
  CONSTRAINT fk_pso_crop_image FOREIGN KEY (crop_image_id) REFERENCES product_select_images (id) ON DELETE SET NULL,
  INDEX idx_pso_content (content_id),
  INDEX idx_pso_content_active (content_id, is_active),
  INDEX idx_pso_potential (ecommerce_potential),
  INDEX idx_pso_related_ip (related_ip),
  INDEX idx_pso_category (category),
  INDEX idx_pso_opportunity_score (opportunity_score),
  INDEX idx_pso_opportunity_level (opportunity_score_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product Select 识图物件/商品机会';

-- 同款/商品匹配：Google Lens、Amazon、淘宝/1688 等返回的商品候选
CREATE TABLE IF NOT EXISTS product_select_matches (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  object_id INT NOT NULL COMMENT '对应识图物件 id',
  source VARCHAR(64) NOT NULL COMMENT '数据源：google_lens|amazon|taobao|1688|aliexpress 等',
  match_level VARCHAR(32) NULL COMMENT 'exact|close|similar|unrelated（后续二次筛选使用）',
  title VARCHAR(512) NULL COMMENT '商品标题',
  store VARCHAR(255) NULL COMMENT '来源店铺/站点',
  url TEXT NULL COMMENT '商品链接',
  price DECIMAL(12,2) NULL COMMENT '数值价格',
  currency VARCHAR(16) NULL COMMENT '币种',
  rating DECIMAL(4,2) NULL COMMENT '评分',
  reviews INT NULL COMMENT '评论数',
  in_stock BOOLEAN NULL COMMENT '是否有货',
  thumbnail_url TEXT NULL COMMENT '缩略图 URL',
  selection_order INT NULL COMMENT '筛选后展示顺序；用于前端优先展示 3/4 个候选',
  selection_role VARCHAR(64) NULL COMMENT '候选来源角色：first_result|quality_selected|amazon_reference',
  selection_score DECIMAL(6,2) NULL COMMENT '候选基础质量分：价格、评论、评分、图片等启发式评分',
  visual_similarity_score DECIMAL(6,2) NULL COMMENT 'Qwen-VL 判断的视觉相似度 0-100',
  keyword_similarity_score DECIMAL(6,2) NULL COMMENT '标题/关键词相似度 0-100',
  final_similarity_score DECIMAL(6,2) NULL COMMENT '最终综合相似度 0-100',
  similarity_level VARCHAR(32) NULL COMMENT '相似度等级：high|medium|low|reject',
  is_reference_used BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否参与商品机会价格/尺寸等参数预估',
  selection_reason TEXT NULL COMMENT '筛选、相似度判断和是否采用的原因',
  raw_json JSON NULL COMMENT '原始匹配条目 JSON，保留未来可用字段',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  CONSTRAINT fk_psm_object FOREIGN KEY (object_id) REFERENCES product_select_objects (id) ON DELETE CASCADE,
  INDEX idx_psmatch_object (object_id),
  INDEX idx_psmatch_source (source),
  INDEX idx_psmatch_price (price),
  INDEX idx_psmatch_match_level (match_level),
  INDEX idx_psmatch_object_selection (object_id, selection_order),
  INDEX idx_psmatch_object_reference (object_id, is_reference_used),
  INDEX idx_psmatch_object_similarity (object_id, final_similarity_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product Select 同款/商品匹配结果';

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
  confidence_score DECIMAL(6,2) NULL COMMENT '本次参数预估可信度 0-100',
  estimate_detail_json JSON NULL COMMENT '参数预估依据：采用/排除的相似商品、价格计算等详情',

  is_active BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否当前生效版本',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  CONSTRAINT fk_psop_object FOREIGN KEY (object_id) REFERENCES product_select_objects (id) ON DELETE CASCADE,
  CONSTRAINT fk_psop_match FOREIGN KEY (reference_match_id) REFERENCES product_select_matches (id) ON DELETE SET NULL,
  INDEX idx_psop_object (object_id),
  INDEX idx_psop_object_active (object_id, is_active),
  INDEX idx_psop_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product Select 商品机会规划/预测（采购售价区间、尺寸重量）';

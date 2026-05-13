-- 为 generations 增加 segment_id，便于串行多段视频在首段提交时预创建记录并在后续提交时更新同一条 Generation。
ALTER TABLE generations
  ADD COLUMN segment_id INT NULL COMMENT '视频 thread 分镜段序号（与 graph task_results.segment_id 对齐）' AFTER thread_id;

CREATE INDEX idx_generations_thread_segment ON generations (thread_id, segment_id);

-- 添加监测账号的测试数据
INSERT INTO product_select_monitors
(platform, handle, display_name, score, monitor_type, is_enabled)
VALUES
('instagram', 'csgoniko', 'NiKo（CS 电竞选手 Nikola Kovač）', 8.00, 'profile', TRUE),
('instagram', 'cristiano', 'C 罗（Cristiano Ronaldo）', 9.00, 'profile', TRUE),
('instagram', 'kyliejenner', 'Kylie Jenner', 8.50, 'profile', TRUE),
('instagram', 'kimkardashian', 'Kim Kardashian', 8.00, 'profile', TRUE),
('instagram', 'kendalljenner', 'Kendall Jenner', 8.00, 'profile', TRUE),
('instagram', 'badgalriri', 'Rihanna', 8.50, 'profile', TRUE),
('instagram', 'selenagomez', 'Selena Gomez', 7.50, 'profile', TRUE),
('instagram', 'zendaya', 'Zendaya', 8.00, 'profile', TRUE),
('instagram', 'leomessi', '梅西（Lionel Messi）', 8.50, 'profile', TRUE),
('instagram', 'taylorswift', 'Taylor Swift', 9.00, 'profile', TRUE),
('instagram', 'virat.kohli', 'Virat Kohli', 7.50, 'profile', TRUE);
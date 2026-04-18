# Commander 配置说明

本文档对应 [config.json](config.json) 与 [commander.ini](commander.ini) 的职责划分和使用方式。

## 配置来源与优先级

1. CLI 参数（最高优先级）
2. [config.json](config.json)
3. 缺失必填项时直接报错退出（不回退硬编码默认值）

说明：
- [commander.ini](commander.ini) 仅用于角色目标映射（按 section 定义 host/port）。
- [config.json](config.json) 管理 commander 目录其余运行参数。

## 顶层结构

- `server`: commander TCP 监听与连接参数
- `scanner`: 扫描周期与统一任务目录
- `storage`: 文件锁与回报文本存储限制
- `dispatch`: dispatch.py 网络与超时参数
- `generator`: 任务生成质量门槛、重试与 opencode 路径
- `paths`: commander 相关路径（相对路径基于 commander/）
- `logging`: 日志级别与轮转参数

## 参数清单

### server

- `host`: commander 监听地址
- `port`: commander 监听端口
- `max_line_bytes`: TCP 单行最大字节数
- `recv_chunk_bytes`: socket 每次 recv 的分块大小
- `socket_timeout_seconds`: 每个连接的超时秒数
- `listen_backlog`: server socket backlog

### scanner

- `data_dir`: 统一任务文件目录（相对路径按 commander/ 解析）
- `scan_interval_seconds`: 扫描循环间隔

### storage

- `lock_timeout_seconds`: 文件锁超时时间
- `max_store_text`: stdout/stderr 入库最大字符数

### dispatch

- `soldier_timeout_seconds`: dispatch 到 soldier 的网络超时
- `client_timeout_seconds`: commander 调用 dispatch.py 的子进程超时
- `timeout_minutes`: 下发任务后写入 expiry_time 的分钟数

### generator

- `min_tasks_per_role`: 每个角色最小任务数
- `min_non_five_ratio`: 非整 5 分钟任务比例阈值
- `max_attempts`: 生成重试次数
- `opencode_timeout_seconds`: 单次 opencode 调用超时
- `opencode_paths_common`: 所有系统都会先尝试的命令候选
- `opencode_paths_windows`: Windows 额外候选路径
- `opencode_paths_linux`: Linux 额外候选路径
- `opencode_paths_macos`: macOS 额外候选路径

### paths

- `logs_dir`: commander/dispatch 日志目录
- `target_ini_file`: 角色目标映射 ini 文件
- `dispatch_script`: scanner 使用的 dispatch 脚本路径
- `domain_resource_file`: 任务生成时使用的领域资源文件

### logging

- `level`: 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- `backup_count`: 日志保留份数
- `rotation_interval_days`: 日志轮转天数间隔

## CLI 覆盖项

### commander.py

- `--host` 覆盖 `server.host`
- `--port` 覆盖 `server.port`
- `--data-dir` 覆盖 `scanner.data_dir`

### dispatch.py

- `--data-dir` 覆盖 `scanner.data_dir`
- `--timeout-minutes` 覆盖 `dispatch.timeout_minutes`
- `--config` 覆盖 `paths.target_ini_file`

## 示例

启动 commander（使用 config.json 默认配置）：

```bash
cd commander
python commander.py
```

覆盖监听端口：

```bash
cd commander
python commander.py --host 127.0.0.1 --port 39001
```

手动下发并覆盖任务目录与超时分钟：

```bash
cd commander
python dispatch.py --target=local --command='echo hi' --task='health-check' --data-dir ./role_task --timeout-minutes 2
```

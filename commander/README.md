# Commander 组件说明

## 概述
Commander 负责统一任务文件生命周期、定时扫描分发、接收 soldier 回报并更新状态。

## 当前架构（重构后）

架构图与依赖关系请见：`ARCHITECTURE.md`

开发者上手清单请见：`DEVELOPER_ONBOARDING.md`

运行参数与字段说明请见：`CONFIG.md`

- `commander.py`：入口层（参数解析、服务启动、TCP 接收）
- `repository.py`：任务文件仓储（锁、读写、状态回写）
- `role_file_service.py`：每日任务文件确保/修复/生成
- `scanner_service.py`：扫描应用服务（角色循环、指针推进、调度流程）
- `policies.py`：任务选择策略（默认最早 pending）
- `dispatch_client.py`：下发适配器（调用 `dispatch.py`）
- `domain.py`：状态迁移规则（planned -> waiting -> successed/failed）
- `logging_setup.py`：统一日志初始化
- `target_config.py`：目标角色配置读取（dispatch 使用）
- `dispatch.py`：手动下发入口
- `generate_role_task.py`：独立任务生成入口

## 快速理解依赖

- 入口：`commander.py` 只做装配与启动。
- 扫描业务：`scanner_service.py` + `policies.py`。
- 文件与状态：`repository.py` + `domain.py`。
- 文件生命周期：`role_file_service.py`。
- 分发适配：`dispatch_client.py`（调用 `dispatch.py`）。

## 统一任务文件

位置：`commander/role_task/tasks_MM-DD.json`

任务项字段：
- `time`
- `is_load`
- `task`
- `task_id`
- `status`
- `issued_at`
- `expiry_time`
- `completed_at`
- `report_message`
- `exit_code`
- `stdout`
- `stderr`

说明：
- 文本字段统一使用 `task`，不再使用 `description`。
- `status` 合法流转由 `domain.py` 统一约束。

## 启动与使用

默认运行参数来自 `config.json`，CLI 参数可覆盖对应字段。

### 启动 commander

```bash
python commander.py
```

可选参数：

```bash
python commander.py --host 0.0.0.0 --port 38471 --data-dir ./role_task
```

### 手动下发任务

```bash
python dispatch.py --target=hr --command='opencode run "使用Exchange查看邮件"' --task='使用Exchange查看邮件'
```

兼容参数：
- `--description` 仍可用，但已标记为兼容别名，推荐使用 `--task`。

## 调度语义

- 每分钟扫描一次。
- 每个角色独立检查 waiting，不互相阻塞。
- 指针优先定位最早 pending 任务。
- `is_load=true` 且 `task_id` 为空的任务会在重启后优先重试，不会被跳过。

## 日志

- 路径：`commander/logs/`
- 文件：`commander_YYYY-MM-DD.log`、`dispatch_YYYY-MM-DD.log`
- 轮转：每日轮转，保留 7 天

## 依赖

```bash
pip install filelock>=3.13.0
```

并确保 `opencode` 可执行文件在 PATH 中。

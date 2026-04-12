# Commander 端脚本说明

## 概述
Commander 是任务管理端，负责生成任务计划、分发任务、接收任务执行结果。

## 脚本列表

### 1. commander.py - 主服务
**作用**：TCP 服务，接收任务执行报告 + 自动任务扫描分发
**功能**：
- 监听 TCP 端口 (默认 38471) 接收 soldier 报告
- 每分钟自动扫描任务计划文件
- 按时间顺序分发任务到 soldier
- 更新任务状态到任务文件

**用法**：
```bash
# 基本启动
python commander.py

# 自定义参数
python commander.py --host 0.0.0.0 --port 38471 --data-dir ./role_task
```

**关键特性**：
- 自动生成每日统一任务文件 (tasks_MM-DD.json)
- 智能调度：按时间顺序，控制并发
- 角色隔离：每个角色按自身状态独立下发，互不阻塞
- 错误恢复：生成失败重试，网络异常处理

---

### 2. dispatch.py - 任务分发器

**作用**：手动分发单个任务到指定 soldier
**功能**：
- 在统一任务文件中更新目标任务的执行状态字段
- 发送任务命令到 soldier
- waiting 仅检查目标角色，其他角色状态不影响当前分发
- 支持任务超时设置

**用法**：
```bash
# 分发任务到 HR 角色
python dispatch.py --target=hr --command='opencode run "使用Exchange查看邮件"'

# 完整参数
python dispatch.py --target=finance --command="echo test" --description="测试任务" --timeout-minutes=30
```

**参数说明**：
- `--target`：目标角色 (必须匹配 commander.ini 配置)
- `--command`：执行的 shell 命令
- `--description`：任务描述
- `--timeout-minutes`：任务超时时间 (默认 10 分钟)

---

### 3. generate_role_task.py - 任务生成器
**作用**：生成每日角色任务计划
**功能**：
- 读取 domain_resource.md 作为上下文
- 调用 opencode CLI 生成智能任务序列
- 保存为 tasks_MM-DD.json 文件（位于 role_task 目录）

**用法**：
```bash
# 生成今日任务计划
python generate_role_task.py

# 自动在 commander.py 启动时调用，也可手动执行
```

**输出格式** (tasks_MM-DD.json)：
```json
{
  "hr": [
    {"time": "09:15", "is_load": false, "task": "使用Exchange查看邮件"},
    {"time": "10:30", "is_load": false, "task": "在OA系统中处理审批"}
  ],
  "finance": [...],
  "ceo": [...],
  "developer": [...]
}
```

---

## 配置文件 (commander.ini)

### 角色配置
```ini
[hr]          # HR 角色
host = 127.0.0.1
port = 38472

[finance]     # 财务角色
host = 127.0.0.1
port = 38472

[ceo]         # 总经理角色
host = 127.0.0.1
port = 38472

[developer]   # 程序员角色
host = 127.0.0.1
port = 38472
```

### 说明
- 每个 `[section]` 对应一个角色
- `host`：soldier 主机地址
- `port`：soldier 监听端口
- 支持分布式部署：修改 host 为实际 IP

---

## 数据文件

### 统一任务文件 (role_task/tasks_MM-DD.json)
```json
{
  "hr": [
    {
      "task_id": "hex_uuid",
      "description": "任务描述",
      "status": "waiting|successed|failed",
      "issued_at": "ISO时间戳",
      "expiry_time": "ISO时间戳",
      "completed_at": "ISO时间戳"
    }
  ]
}
```

- 单文件同时承载任务计划与执行状态
- 自动每日生成
- 包含所有角色的任务序列与运行态字段
- 时间扰动与任务因果关联

---

## 工作流程

### 自动模式 (推荐)
```bash
# 1. 启动 commander (自动包含所有功能)
python commander.py

# 自动执行：
# - 生成今日任务计划 (如不存在)
# - 每分钟扫描并分发任务
# - 接收 soldier 报告
# - 更新任务状态
```

### 手动模式
```bash
# 1. 生成任务计划
python generate_role_task.py

# 2. 启动 commander 接收报告
python commander.py

# 3. 手动分发特定任务
python dispatch.py --target=hr --command='opencode run "具体任务描述"'
```

---

## 日志系统
- **位置**：`logs/commander_YYYY-MM-DD.log`
- **轮转**：每日轮转，保留 7 天
- **内容**：连接、分发、错误等详细记录

---

## 依赖要求
```bash
# 安装依赖
pip install filelock

# 系统要求
- Python 3.8+
- opencode CLI (在 PATH 中)
```

---

## 故障排除

### 1. opencode CLI 不可用
```
症状：任务生成失败
解决：确保 opencode 在系统 PATH 中
```

### 2. 端口冲突
```
症状：启动失败 "Address already in use"
解决：更换端口或停止占用进程
```

### 3. 文件权限
```
症状：无法创建数据文件
解决：检查 data/ 目录读写权限
```

### 4. 网络连接
```
症状：无法连接到 soldier
解决：检查 commander.ini 配置和网络连通性
```

---

## 快速开始

### 本地测试
```bash
# 1. 启动 soldier (在另一个终端)
cd ../soldier
python soldier.py listen

# 2. 启动 commander
cd ../commander
python commander.py

# 3. 查看日志
tail -f logs/commander_*.log
```

### 分布式部署
1. 修改 `commander.ini` 中的 host 为实际 IP
2. 确保防火墙开放相应端口
3. 分别在 commander 和 soldier 机器启动服务
# Soldier 端脚本说明

## 概述
Soldier 是任务执行端，负责接收并执行 commander 分发的任务，然后报告执行结果。

## 主脚本：soldier.py

### 作用
- 监听 TCP 连接接收任务
- 执行 shell 命令
- 向 commander 报告执行结果
- 支持手动报告模式

### 运行模式

#### 1. 监听模式 (默认)
持续监听任务并自动执行。

```bash
# 基本启动
python soldier.py listen

# 自定义参数
python soldier.py listen --bind 0.0.0.0 --listen-port 38472 --commander-host 127.0.0.1 --commander-port 38471
```

#### 2. 报告模式
手动提交任务执行报告。

```bash
# 报告成功任务
python soldier.py report --task-ref "2026-04-01_hr_a1b2c3d4e5f67890" --status successed --exit-code 0

# 报告失败任务
python soldier.py report --task-ref "04-01_finance_b3c4d5e6f7890123" --status failed --exit-code 1 --stderr "错误信息"
```

---

## 配置文件 (soldier.ini)

### 默认配置
```ini
[commander]
ip = 127.0.0.1      # commander 地址
port = 38471        # commander 端口

[listen]
bind = 0.0.0.0      # 监听地址
port = 38472        # 监听端口

[exec]
timeout = 3600      # 命令超时时间 (秒)
```

### 配置说明
- **优先级**：命令行参数 > 配置文件 > 默认值
- **分布式部署**：修改 `[commander]` 节的 IP 为实际 commander 地址
- **安全考虑**：生产环境建议绑定特定 IP

---

## 任务处理流程

### 1. 接收任务
从 commander 接收 JSON 格式任务：
```json
{
  "task_ref": "2026-04-01_hr_a1b2c3d4e5f67890",
  "command": "opencode run \"使用Exchange查看邮件\"",
  "task_date": "2026-04-01"
}
```

### 2. 执行命令
在本地 shell 中执行命令：
- 默认超时：3600 秒 (1小时)
- 捕获标准输出和错误输出
- 记录退出代码

### 3. 报告结果
向 commander 发送执行结果：
```json
{
  "task_ref": "2026-04-01_hr_a1b2c3d4e5f67890",
  "status": "successed|failed",
  "exit_code": 0,
  "stdout": "命令输出",
  "stderr": "错误输出"
}
```

### 4. 保存记录
任务接收记录保存到本地文件：
- **位置**：`received_task_MM-DD.jsonl`（位于 soldier.py 同级目录）
- **格式**：每行一个 JSON，包含 `task_id`、`content`、`received_at`
- **清理**：自动清理 20 天前的记录

---

## 命令行参数

### 全局参数
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config` | 配置文件路径 | `soldier.ini` |

### 监听模式参数
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--bind` | 监听地址 | `0.0.0.0` |
| `--listen-port` | 监听端口 | `38472` |
| `--commander-host` | commander 地址 | 配置文件值 |
| `--commander-port` | commander 端口 | 配置文件值 |

### 报告模式参数
| 参数 | 必填 | 说明 |
|------|------|------|
| `--task-ref` | 是 | 任务引用：`date_role_taskId` |
| `--status` | 是 | 状态：`successed` 或 `failed` |
| `--message` | 否 | 报告消息 |
| `--exit-code` | 否 | 退出代码 |
| `--stdout` | 否 | 标准输出 |
| `--stderr` | 否 | 标准错误 |
| `--host` | 否 | commander 地址 |
| `--port` | 否 | commander 端口 |

---

## 任务引用格式

### 完整格式
```
YYYY-MM-DD_role_taskId
```
示例：`2026-04-01_hr_a1b2c3d4e5f67890`

### 日期格式
- **完整日期**：`YYYY-MM-DD` (如 `2026-04-01`)
- **简写日期**：`MM-DD` (如 `04-01`，自动补全当前年份)

### 角色名称
- 必须不包含下划线
- 建议小写字母
- 示例：`hr`, `finance`, `ceo`, `developer`

### 任务 ID
- UUID 十六进制格式 (无连字符)
- 长度：8-32 字符
- 示例：`a1b2c3d4e5f67890`

---

## 执行环境

### 命令执行
- **Shell**：系统默认 shell
- **工作目录**：soldier.py 所在目录
- **用户权限**：运行 soldier 进程的用户权限
- **输出编码**：UTF-8

### 安全限制
⚠️ **注意**：soldier 执行传入的任何命令，无内置限制
- 建议在受控环境运行
- 考虑使用容器隔离
- 最小权限原则

### 超时控制
- 默认：3600 秒 (1小时)
- 配置：通过 `soldier.ini` 的 `[exec] timeout` 参数
- 超时处理：终止进程，报告超时错误

---

## 数据文件

### 任务记录文件
```
received_task_MM-DD.jsonl
```
内容示例：
```
{"task_id":"a1b2c3d4e5f67890","received_at":"2026-04-12T09:10:00+00:00","content":{"task_ref":"2026-04-12_hr_a1b2c3d4e5f67890","command":"opencode run ..."}}
{"task_id":"b3c4d5e6f7890123","received_at":"2026-04-12T09:40:00+00:00","content":{"task_ref":"2026-04-12_finance_b3c4d5e6f7890123","command":"opencode run ..."}}
```

### 日志文件
```
logs/soldier_YYYY-MM-DD.log
```
- 每日轮转，保留 7 天
- 包含连接、执行、报告等详细信息

---

## 使用示例

### 示例 1：本地测试
```bash
# 1. 启动 soldier (监听模式)
python soldier.py listen

# 输出示例：
# Soldier starting, logs: logs/soldier_2026-04-01.log
# Listening for tasks on 0.0.0.0:38472; reporting to commander 127.0.0.1:38471; exec timeout=3600s
```

### 示例 2：自定义网络配置
```bash
# soldier 在 192.168.1.100，commander 在 192.168.1.50
python soldier.py listen \
  --bind 192.168.1.100 \
  --listen-port 49000 \
  --commander-host 192.168.1.50 \
  --commander-port 38471
```

### 示例 3：手动报告任务
```bash
# 报告成功任务
python soldier.py report \
  --task-ref "2026-04-01_hr_a1b2c3d4e5f67890" \
  --status successed \
  --exit-code 0 \
  --stdout "任务执行成功" \
  --message "所有步骤完成"

# 报告失败任务
python soldier.py report \
  --task-ref "04-01_finance_b3c4d5e6f7890123" \
  --status failed \
  --exit-code 1 \
  --stderr "文件未找到: /path/to/file.txt"
```

### 示例 4：使用自定义配置文件
```bash
# 创建配置文件
cat > custom.ini << EOF
[commander]
ip = 10.0.0.10
port = 49001

[listen]
bind = 10.0.0.100
port = 49002

[exec]
timeout = 7200
EOF

# 使用自定义配置
python soldier.py --config custom.ini listen
```

---

## 故障排除

### 常见问题

#### 1. 端口被占用
```bash
# 检查端口占用
netstat -ano | findstr :38472

# 解决方案：更换端口
python soldier.py listen --listen-port 38473
```

#### 2. 无法连接 commander
```
错误：Failed to connect to commander: [Errno 10061] 由于目标计算机积极拒绝，无法连接。
```
**解决**：
- 检查 commander 服务是否运行
- 验证 commander 地址和端口
- 检查防火墙设置

#### 3. 命令执行失败
```
错误：执行失败: [Errno 2] No such file or directory: 'nonexistent-command'
```
**解决**：
- 验证命令路径和权限
- 检查环境变量
- 使用完整命令路径

#### 4. 配置文件错误
```
错误：configparser.NoSectionError: No section: 'commander'
```
**解决**：
- 检查配置文件格式
- 验证配置文件路径
- 使用示例配置重新创建

### 调试技巧

#### 查看实时日志
```bash
tail -f logs/soldier_*.log
```

#### 测试 commander 连接
```bash
# 使用 telnet 测试
telnet 127.0.0.1 38471
```

#### 手动发送测试任务
```bash
# 使用 netcat 发送测试任务
echo '{"task_ref": "2026-04-01_test_1234567890abcdef", "command": "echo test", "task_date": "2026-04-01"}' | nc 127.0.0.1 38472
```

---

## 系统集成

### 与 Commander 集成
1. soldier 配置中指定 commander 地址
2. 执行任务后自动报告
3. 处理 commander 响应

### 多实例部署
```bash
# 实例 1 (端口 38472)
python soldier.py listen --listen-port 38472

# 实例 2 (端口 38473)
python soldier.py listen --listen-port 38473 --commander-port 38471
```

### 负载均衡
通过多个 soldier 实例实现：
1. 在不同端口启动多个 soldier
2. commander 配置多个目标
3. 实现简单的负载分发

---

## 依赖要求
```bash
# 安装依赖
pip install filelock

# 系统要求
- Python 3.8+
- 网络连通性 (与 commander)
```

---

## 安全建议

### 生产环境部署
1. **环境隔离**：使用专用用户运行
2. **网络隔离**：部署在内网环境
3. **权限控制**：最小权限原则
4. **日志审计**：定期检查日志文件

### 容器化部署
```dockerfile
# Dockerfile 示例
FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt soldier.py soldier.ini ./

RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/logs

CMD ["python", "soldier.py", "listen"]
```

### 监控告警
- 监控 soldier 进程状态
- 监控端口监听状态
- 设置错误日志告警
- 监控任务执行成功率
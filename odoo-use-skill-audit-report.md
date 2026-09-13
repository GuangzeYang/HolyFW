# odoo-use Skill 现象与整改报告

- 日期：2026-09-11
- 样本任务：`cb6f5ce6ce164e01`
- 角色：HR
- 目标系统：Odoo 17 Recruitment（`http://172.16.24.14:8069/`）
- 用途：目标端拉起 agent 后，按第 6 节打开网站自审；确认后再改 skill / 模板 / 任务生成约束

本报告结论：**超时不是 Playwright MCP 教错步骤，而是 `odoo-use` 的 Recruitment 更新路径在真实页面上不可唯一执行，且 `email address` 被映射成必须唯一的 Job Email Alias。** Playwright MCP 主要把失败后的迂回放大到 900 秒。

---

## 1. 现场现象

Soldier 日志：

```text
2026-09-11 12:08:59  Received  cb6f5ce6ce164e01
2026-09-11 12:23:59  WARNING   Fail Command timeout (>900s); process tree terminated
```

任务原文（语法合格，和模板一致）：

```text
Use the odoo-use skill, log in to the Odoo system, use the Recruitment module,
update job posting, {job position: Lead UX Designer, email address: hr@ndrtest.local}
```

关键事实：

1. `opencode` 进程在 900 秒墙钟上限被 `soldier` 杀进程树。WARNING 是 Fail 的日志级别，不是 Playwright / Odoo 自己在刷警告。
2. 日志预览只有 stdout 前 500 字，所以看起来停在第一段 `Thinking:`。完整记录见 `cb6f5ce6ce164e01.md`（4300+ 行）。最后一次页面快照是 `12:23:58`，下一秒被杀。
3. 交互没有死锁。登录、进 Recruitment、最终打开 Lead UX Designer 表单都成功了。被杀时模型还在点 **Save manually**。
4. 业务目标未完成：Email Alias 没有写成 `hr@ndrtest.local`。

同窗口里 11 条任务只有 2 条 WARNING，都是浏览器多步任务顶满 900 秒（另一条是 Exchange）。短任务 37 秒到 11 分钟可成功。

---

## 2. 执行时间线（样本）

| 北京时间 | 行为 | 结果 |
|---|---|---|
| 12:08:59 | 收到任务，load `odoo-use` + `playwright-browser` | 正常 |
| 12:09:43 | 打开 `/web/login`，填 HR 账号 | 成功 |
| 12:10:20 | 登录后落到 Discuss | 成功 |
| 12:10:48 | `button[title='Home Menu']` 打开 waffle | 成功（skill 这条是对的） |
| 12:11:22 | 进入 Recruitment / Job Positions kanban | 成功 |
| 12:11:57 | `a:has-text("1 To Recruit").nth(25)` | **点错**，打开 Junior Front-end Developer |
| 12:13:53 | 用 CSS nth-child 再点 | 终于打开 Lead UX Designer（id=56） |
| 12:14–12:16 | 按 skill 把 `hr@ndrtest.local` 拆进 alias 两个框后 Save | Odoo 弹窗：`Alias hr` 已被 UI/UX Designer 占用 |
| 12:16:10 | Discard changes | 表单清空 |
| 12:16–12:23 | 改成 `lead-ux-designer`、回 kanban、再搜索、再打开、再填域名 | 域名下拉 **No records**；Save 仍未成功 |
| 12:23:59 | soldier 超时杀进程 | Fail |

登录和进模块没有问题。翻车从「在 kanban 上点对卡片」开始，随后被 Email Alias 业务约束拖死。

---

## 3. 根因排序

| 占比 | 来源 | 作用 |
|---|---|---|
| ~45% | `odoo-use` skill | kanban 点击不可唯一；alias 冲突 / 域名 Many2one 无失败路径 |
| ~35% | 任务取值（模板可填、生成器无约束） | 把角色邮箱 `hr@ndrtest.local` 填进必须唯一的 Job Alias |
| ~10% | 模型偏离 skill | `nth(25)`、`run_code_unsafe`、Discard 后继续绕 |
| ~10% 错步骤 / 50–70% 耗时 | Playwright MCP + `playwright-browser` | 同名控件、整页 snapshot、强制先 snapshot |

四个角色的 `odoo-use` 是同一套 Recruitment 文本，不要只改 HR：

- `role_profiles/hr-skills/odoo-use/SKILL.md`
- `role_profiles/manager-skills/odoo-use/SKILL.md`
- `role_profiles/accountancy-skills/odoo-use/SKILL.md`
- `role_profiles/programmer-skills/odoo-use/SKILL.md`

---

## 4. Skill 问题（主因）

### 4.1 `update job posting` 没有强制 Search，To Recruit 在页面上不唯一

当前写法：

```text
Open Recruitment. Click the N To Recruit link (a[name=edit_job])
on the card whose title matches job position (or click the job title).
```

真实 Job Positions kanban：

- 每张卡都有可见文本 `1 To Recruit` / `N To Recruit`
- 每张卡的编辑链接都是 `a[name=edit_job]`
- Playwright MCP 的一等定位是 accessible name / role / snapshot ref，**不能**把「标题匹配的那张卡上的 To Recruit」当成一次原子点击

样本里模型把这句话翻译成：

```js
await page.locator('a:has-text("1 To Recruit")').nth(25).click();
```

点进了 Junior Front-end Developer。之后三次 `run_code_unsafe` 扫 `article.innerText()`，Odoo kanban 经常返回空字符串，于是 `not found`。这已经违反 `playwright-browser` 的「只调用现有 MCP、不要自己写自动化」。

Shared form 其实有 Search 规则，Employees 的 open 也是「先 Search 再点卡」。Recruitment 的 update/delete **没有**把 Search 写成第一步，模型就不会先搜。

**自审时要确认：** 未过滤的 kanban 上，「To Recruit」链接数量是否远大于 1；Search `Lead UX Designer` 后是否只剩一张卡。

### 4.2 `email address` 被实现成 Job Email Alias，skill 只写了 happy path

Skill 把任务字段 `email address` 映射为：

- 前缀 → `#alias_name_0`（placeholder `e.g. sales-manager`）
- 域名 → `#alias_domain_id_0`（placeholder `e.g. domain.com`）

并套用 Shared Many2one 规则：「如果下拉只剩 **Create ...**，就点 Create」。

样本里真实页面是：

1. Lead UX Designer 的 Email Alias 前缀框原本是空的。
2. 填 `hr` 后 Save，Odoo 弹窗：

   ```text
   Oh snap!
   Alias hr ([56]) is already linked with Applicant (10)
   and used by the UI/UX Designer Job Position.
   Choose another value or change it on the other document.
   ```

3. 域名框输入 `ndrtest.local` 后，下拉是 **No records**，不是 **Create ...**。这个 Many2one 不能在职位表单里 quick-create。
4. Skill 对 Save 仍可见的处理是 Discard 然后 **Stop**。角色 `AGENTS.md` 又要求失败就自己换方案继续。模型 Discard 后改 prefix、回列表、再打开，烧了约 8 分钟。

**Skill 缺三件事：**

- alias 已被其他 Job / Applicant 占用时：Discard，任务失败，不要改成别的 prefix
- 域名下拉是 No records 时：不要按 Create 规则死磕；记录现状后停
- `email address` 与角色邮箱（`hr@ndrtest.local`）不是同一类字段，不能默认复用

### 4.3 打开卡片的两种说法互相打架

Shared form：

```text
Open a kanban record: click the card div whose title matches the name.
```

Recruitment update：

```text
Click the N To Recruit link ... (or click the job title).
```

模型先信了 To Recruit（全局同名），后又去点 article / CSS nth-child。自审时要选出 **一条** Playwright 可执行的打开方式，写进 skill，删掉另一条。

建议主路径（待网站确认后定稿）：

1. Search... 输入职位名，Enter，等 kanban 只剩目标卡
2. 点这张卡的 **职位标题**（不是全局 `To Recruit`）
3. 确认 URL 含该记录 id，Title 为 `Odoo - <job position>`

`a[name=edit_job]` 只能作为「Search 之后当前页只剩一个」时的备用，禁止 `nth(N)`。

### 4.4 create 路径有同样的 alias / 域名假设

`create job posting` 也是 split `@` 填两个框。若库里没有 `ndrtest.local` 这条 alias domain，create 同样会在对话框里失败。模板示例却是：

```text
{job position: Human Resources Manager, email address: jobs@ndrtest.local}
```

自审必须连 **New → Create a Job Position** 对话框一起看，不要只看法单编辑页。

### 4.5 Playwright 放大，但不是教错方法的主因

`playwright-browser` 要求每次 click/type 前先 snapshot。Odoo Recruitment kanban / form 的 a11y tree 极大，样本约 55 次 Playwright 调用。这解释了为什么「还在正常点」也会顶满 900 秒。

MCP 没有发明 `nth(25)`，也没有发明把 `hr@` 写进 alias。它执行的是 skill 给的人类描述，而那条描述在 MCP 里不能唯一解析。

整改时不要把 Odoo 步骤改回通用 `playwright-browser` 搜索配方。Odoo overlay 可以加：禁止 `nth` 点 To Recruit；Search 命中后只用当前 snapshot 里带目标标题的那个 ref。

---

## 5. 模板与任务生成的连带问题

`odoo-use/templates.md` 的 grammar 没有错。缺的是取值约束和 update 示例。

1. 只有 create 示例 `jobs@ndrtest.local`，没有 update job posting 示例。
2. `task_generation_constraints.md` 要求 create/add 名称唯一，**没有**要求 Job Alias 唯一，也没有禁止把角色邮箱填进 `email address`。
3. `role_profiles/AGENTS.md` 里 HR 邮箱就是 `hr@ndrtest.local`。规划器会把「HR 的邮箱」填进 Recruitment 字段。
4. 字段名叫 `email address`，skill 实现的是 application alias。联系邮箱和投递别名不是一回事。

建议生成器硬约束：

- Job `email address` 不得使用角色邮箱：`hr@` / `manager@` / `accountancy@` / `programmer@`
- 前缀必须相对当天已有 Job Alias 唯一，例如 `lead-ux-designer@<已存在的 alias domain>`
- 若自审确认库里没有 `ndrtest.local` 这条 domain 记录，就不要再生成 `@ndrtest.local`；改用页面上真实存在的 domain，或任务不要带 `email address`

---

## 6. 目标端网站自审清单

目标端 agent 只做只读核对，**不要 Save 到会改坏现网数据的程度**。需要试 Save 时，填完立刻 Discard。账号、URL 用已安装的 `odoo-use` skill，不要另造密码。

### 6.1 登录与进模块（预期应通过）

1. 打开 skill 中的 `/web/login`。
2. 填 Email / Password，点 Log in。
3. 确认 `getByRole('button', { name: 'Home Menu' })` 经常找不到。
4. 用 `button[title='Home Menu']` 或 waffle 打开应用列表，点 Recruitment。
5. 记录：Home Menu 的可用选择器、Recruitment 打开后的 URL 与标题。

### 6.2 Kanban 定位（skill 主缺陷，必查）

在 **Job Positions** 未过滤视图：

1. 数有多少张卡，多少个可见文本含 `To Recruit`。
2. 在 snapshot 里数有多少个 `a[name=edit_job]`。预期：远大于 1。
3. 找 Lead UX Designer 卡片，分别记下：
   - 职位标题节点的 role / name / ref
   - 该卡上 `To Recruit` 链接的 ref（不要用全局 name）
   - 该卡容器（article / 卡 div）是否能用标题唯一定位
4. **不要**用 `nth(N)` 点 To Recruit。
5. Search... 输入 `Lead UX Designer`，Enter。确认是否只剩一张卡。若是，记录「先 Search 再点标题」是否足够稳定。
6. 点职位标题打开表单。确认 Title / 面包屑 / URL `id=`。
7. 返回 kanban，Search 后若只剩一张，再试一次该卡上的 `To Recruit`，确认和点标题是否打开同一记录。

把自审结论写成三选一，供改 skill：

- A. 先 Search，再点职位标题（推荐候选）
- B. 先 Search，再点该卡 `a[name=edit_job]`
- C. 不 Search，用「包含职位名的 article 内部的 To Recruit」（仅当 MCP 能稳定点到 snapshot ref）

禁止继续写「点匹配卡片上的 N To Recruit」这种人类句子，除非附上 Search 前置条件和「禁止全局 name / 禁止 nth」。

### 6.3 Email Alias 字段（skill 第二缺陷，必查）

打开 Lead UX Designer 表单，只观察，必要时填完 Discard：

1. 记录 **Email Alias** 两个控件的：
   - 可见 label（样本是 `Email Alias?`）
   - 前缀 textbox 的 id / placeholder（skill 写 `#alias_name_0`）
   - 域名 combobox 的 id / placeholder（skill 写 `#alias_domain_id_0`、`e.g. domain.com`）
2. 当前前缀、当前域名各是什么（空也要记）。
3. 点开域名下拉，**不输入**，列出已有 domain 记录原文。`ndrtest.local` 在不在。
4. 输入 `ndrtest.local`，看下拉是 **Create "ndrtest.local"**、匹配行，还是 **No records**。
5. 打开 UI/UX Designer，记录它的 Email Alias 全文。样本显示 `hr` 已被它占用。
6. 打开任意已有 alias 的职位（样本里 Junior Front-end Developer 曾出现 `manager-jrfe`），记录可用的 domain 显示值。
7. 不要把 `hr` 再次 Save 到 Lead UX Designer。若必须验证冲突弹窗，Save 后立刻 Discard，并抄完整 Oh snap 文本。

自审必须回答：

- 这个库的 alias domain 实际值是什么？
- 职位表单能否 Create 新 domain？
- `email address: *@ndrtest.local` 在这个库上是否根本写不进去？
- alias 冲突时正确收口是 Discard+Stop，还是改 prefix？

### 6.4 Create Job 对话框

Recruitment → New → **Create a Job Position**：

1. 对话框里的 Job Position、Email Alias 控件是否仍是 `#name_0` / `#alias_name_0` / `#alias_domain_id_0`。
2. 域名下拉是否同样 No records。
3. 点 Discard 关闭，不要 Create 测试职位，除非用明确可删的一次性名称并随后删除。

### 6.5 配置入口（可选）

若表单里看不清 domain：Recruitment → Configuration，找 alias domain / email server。只读。记下模型名或菜单路径，准备写进 skill 的失败说明（例如「domain 不存在就停，不要去 Apps」）。

### 6.6 Snapshot 体积（解释超时，不是改业务字段）

在未过滤 kanban 和 Lead UX Designer 表单各做一次 snapshot，记录大概节点数。若单页数百节点，skill 应写：

- 不要为每个 hover 再 dump 整页
- Search 缩小结果后再点
- 禁止为调试去 `run_code_unsafe` 扫全部 article

### 6.7 自审输出格式

目标端 agent 按下面填，不要写成长篇推理：

```text
## 自审结果
- Home Menu 可用选择器:
- 未过滤 To Recruit 数量 / 卡片数量:
- Search "Lead UX Designer" 后卡片数:
- 推荐打开方式: A / B / C （理由一句话）
- Lead UX Designer 当前 alias 前缀 / 域名:
- UI/UX Designer 当前 alias 前缀 / 域名:
- 域名下拉输入 ndrtest.local 后的选项原文:
- 可否在职位表单 Create domain: yes / no
- 库中真实 alias domain 列表:
- create 对话框控件 id 是否与 skill 一致: yes / no
- 建议 skill 改动的三句话:
```

---

## 7. 建议整改（等自审结果再改文件）

网站结论回来之前，不要凭样本臆造 domain 名称。方向如下。

### 7.1 四个角色的 `odoo-use/SKILL.md`

`update job posting` / `delete job posting` 改为：

1. Open Recruitment。
2. Search... 输入 `job position`，Enter，等到只剩目标卡（或空结果）。空结果且任务要求命中：Stop。
3. 用自审选定的 A 或 B 打开记录。禁止 `locator('...To Recruit').nth(N)`。禁止 `run_code_unsafe` 遍历 article。
4. 确认标题 / URL 就是目标职位。
5. 只改任务给出的字段。
6. `email address`：按 `@` 拆。前缀写入 alias 前缀框。域名：只点下拉里**已经存在**的匹配行。没有匹配、只有 No records：Discard，Stop，不要 Create domain，不要改成其他 prefix。
7. Save manually。云朵图标消失才算成功。
8. Oh snap / alias already linked / Save 仍可见：Discard，Stop。不要换 prefix 重试。不要回 kanban 再走一遍。

Many2one 的 Create 规则加排除：`#alias_domain_id_0` / Email Alias 域名框不适用 Create。

Anti-patterns 增加：

- Do not click a global `To Recruit` name when more than one card is visible.
- Do not invent `nth` for kanban links.
- Do not replace a colliding alias prefix with a made-up local-part.
- Do not treat the role mailbox as a job application alias.

### 7.2 `templates.md` 与生成约束

- 增加一条 update 示例，alias 使用**自审看到的真实 domain**，前缀用职位短名，例如 `lead-ux@<existing-domain>`，不要用 `hr@ndrtest.local`。
- `task_generation_constraints.md` / `commander/prompt_resources/skill_templates.json`：Job `email address` 禁止角色邮箱；create/update 的 alias 前缀必须唯一。
- 若自审确认没有 `ndrtest.local` domain，模板示例和生成器都停止使用 `@ndrtest.local`。

### 7.3 `playwright-browser`（可选、次要）

加 Odoo overlay（URL 含 `:8069` 或 `/web`）：

- 覆盖「每次 click 前必须整页 snapshot」：同一表单连续填多个已聚焦字段时不要重复 dump
- 禁止对 Odoo kanban 使用 `run_code_unsafe`
- 同名链接必须先 Search 或使用带父级标题的 snapshot ref

### 7.4 不要改的部分

样本已验证可用：

- 登录 URL、`#login`、`#password`、Log in
- `button[title='Home Menu']` / waffle，不要用 `getByRole Home Menu`
- 从 Home Menu 点 Recruitment
- Save manually / Discard changes 的可见名称
- Employees 的「先 Search 再打开」思路（Recruitment 应抄这个，而不是反过来）

---

## 8. 给目标端 agent 的任务提示（可直接粘贴）

```text
Read odoo-use-skill-audit-report.md in the repo root. Load odoo-use and
playwright-browser. Log in to Odoo with the skill credentials. Open
Recruitment. Do a read-only UI audit of Job Positions kanban and the
Lead UX Designer / UI/UX Designer forms plus the Create Job dialog.
Follow section 6 only. Do not save colliding aliases. Discard any test
edits. Return the section 6.7 block and nothing else.
```

---

## 9. 样本证据索引

| 内容 | 位置 |
|---|---|
| 任务 argv / 超时元数据 | `cb6f5ce6ce164e01.md` 文首 YAML |
| 点错 To Recruit `nth(25)` | 同文件约第 1020 行 |
| `run_code_unsafe` innerText 失败 | 约第 1891 行 |
| 打开 Lead UX Designer | 约第 1963 行 |
| Oh snap alias `hr` 冲突 | 约第 2564 行 |
| 域名 No records | 约第 4274 行 |
| 被杀前最后一次 Save | 约第 4332 行 |
| 当前 skill 原文 | `role_profiles/hr-skills/odoo-use/SKILL.md` |
| 900 秒超时 | `soldier/soldier.ini` `timeout = 900` |

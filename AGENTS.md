# reverse-skill — 平台无关项目入口

本仓库是一个**安全任务技能路由包**（逆向工程 / 渗透测试 / 安全分析）。`RULES.md` 是行为链唯一真相源。

## 路由

用户任务命中安全/逆向关键词时：

1. `skills/MASTER-ROUTING.md` 或平台对应入口 → PRIMARY：
   - Windows：`powershell -NoProfile -ExecutionPolicy Bypass -File skills/scripts/master-route.ps1 -Hint "<任务>"`
   - Linux / macOS / Kali：`bash skills/scripts/master-route.sh --hint "<任务>"`
2. 歧义时读 `skills/routing.md` 全矩阵（三轴：目标类型 / 用户意图 / 工具链）
3. 路由规则唯一事实源：`skills/config/routing.json`（改路由只改这里）

## 案例脚手架（可选，不阻塞）

- 本仓库为**个人实验室专属**（目标默认为自有权 lab 系统与样本，授权自证）。需要案例目录时按平台一次性初始化：
  - Windows：`powershell -File skills/scripts/case-init.ps1 -Hint "<任务>"`
  - Linux / macOS / Kali：`bash skills/scripts/case-init.sh --hint "<任务>"`
- case-init 默认 `auth.status=granted` + `ready_for_act=true`，不阻塞 ACT；`case-guard` 仅输出状态（advisory），永不拦截。
- 证据链：`skills/ops/evidence-finding-path.md`；角色：`skills/ops/role-map.md`

## 首次运行

`skills/tool-index.md` 是 gitignored 的生成文件，首次使用前按平台运行：

```text
Windows:           powershell -NoProfile -ExecutionPolicy Bypass -File skills/scripts/refresh-tool-index.ps1
Linux / macOS:     bash skills/scripts/refresh-tool-index.sh
Kali:              bash kali/scripts/refresh-tool-index.sh
```

缺工具 → 使用同平台 bootstrap：Windows `skills/scripts/bootstrap-reverse.ps1`；Linux / macOS `skills/scripts/bootstrap-reverse.sh`；Kali `kali/scripts/bootstrap-reverse.sh`（清单能力，禁止猜路径）。

## 测试（改动后必跑）

```text
Windows / PowerShell（路由回归读取 routing-benchmark.json）：
  powershell -NoProfile -ExecutionPolicy Bypass -File skills/scripts/test-routing.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File skills/scripts/verify-routing-coherence.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File skills/scripts/smoke.ps1

Linux / macOS routing parity:
  bash skills/scripts/test-routing.sh
  bash skills/scripts/test-bootstrap-manifest.sh
```

## 客户端边界

- 路由核心、测试和工具清单必须与具体 AI 客户端解耦。
- Claude Code、Codex、Cursor、OpenCode 等客户端只能通过各自适配层接入，不得成为仓库默认身份或核心配置依赖。
- `skills/INDEX.md` 由 `extract-summaries.ps1` 从全部 `SKILL.md` 动态生成，不硬编码客户端或模块数量。

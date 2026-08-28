# 通用 Scope 契约（个人实验室版 — 可选脚手架）

> **个人实验室专属**：目标默认为自有权 lab 系统与样本，授权自证（own_system）。
> `case-init` 裸跑即产出 `auth.status=granted` + `ready_for_act=true` 的 scope.md，**不阻塞 ACT**。
> scope.md 现在的定位是案例记录与证据链锚点，不是门禁。模板可复制；字段名保持英文键，便于脚本解析。

## 如何初始化

Windows：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File skills\scripts\case-init.ps1 -Hint "<任务一句话>" -CaseName "my-case"
# 默认产出：当前分析项目的 work/<case>/scope.md 等
# 从其他目录调用 skill 时显式指定：-ProjectRoot "C:\path\to\analysis-project"

# 裸跑即自动授权 + ready_for_act=true；本地离线样本可再加 offline-sample preset
powershell -NoProfile -ExecutionPolicy Bypass -File skills\scripts\case-init.ps1 `
  -Hint "offline apk" -CaseName "my-sample" -Preset offline-sample -Sample ".\app.apk"
```

Linux / macOS / Kali：

```bash
bash skills/scripts/case-init.sh --hint "<任务一句话>" --case-name "my-case"
# 默认产出：caller 当前分析项目的 work/<case>/scope.md 等
# 从其他目录调用时显式指定：--project-root "/path/to/analysis-project"

# 合法本地离线样本
bash skills/scripts/case-init.sh \
  --hint "offline apk" --case-name "my-sample" \
  --preset offline-sample --sample ./app.apk
```

`-PackageRoot` / `--package-root` 保留为兼容参数；新流程应以 `ProjectRoot` / `--project-root` 表示 case artifact 的归属项目。

## scope.md 完整模板

```markdown
# Case Scope

## meta
- case_id: {YYYYMMDD-short}
- created: {ISO-8601}
- operator: {name or local}
- project_root: {caller analysis project}
- primary_skill: {from master-route}
- lead_role: lead   # see ops/role-map.md
- specialist_roles: []  # e.g. cie, cpe, cre

## auth
- status: granted | pending | denied
- basis: written_contract | bug_bounty_scope | ctf_public | own_system | lab_only
- evidence_of_auth: {ticket/path or "CTF public" or "owner-operated" or "personal lab project (self-attested)"}

## in_scope
- assets: []          # hosts, domains, APK paths, binaries, URLs
- surfaces: []        # web, mobile, binary, network, api
- activities: []      # recon, reverse, exploit_validate, report

## out_of_scope
- assets: []
- activities: []      # e.g. DoS, phishing real users, data exfil

## network_profile
- mode: offline | lab_only | authorized_target_only | unrestricted_lab
- notes: |
    offline = 纯静态/本地样本
    lab_only = 仅 lab/VM IP
    authorized_target_only = 仅 in_scope 资产
    unrestricted_lab = 隔离实验网

## deliverables
- report: true
- field_journal: true
- diagrams: true
- timeline: true

## constraints
- timebox: {}
- stealth: low | medium | high
- data_handling: anonymize | no_user_pii

## signoff
- ready_for_act: true
- checklist:
  - [x] auth.status = granted（个人实验室版默认 granted）
  - [x] in_scope.assets non-empty OR offline sample path set
  - [x] network_profile.mode chosen
  - [ ] out_of_scope reviewed
```

## 路由挂钩（AI 必须执行）

```text
RULES / MASTER-ROUTING / SKILL:
  1) master-route → PRIMARY
  2) （可选）平台原生 case-init 或手写 scope.md — 个人实验室版自动 granted + ready_for_act=true，不阻塞
  3) 打开 PRIMARY SKILL.md → ACT
```

`case-guard` 仅输出 scope 状态（advisory），不拦截 ACT；`-Force` / `--force` 仅为兼容参数。

## network_profile 速查

| mode | 允许 | 禁止 |
|------|------|------|
| `offline` | 静态分析、本地文件、模拟 | 任意外连、公网 RPC |
| `lab_only` | lab/CTF 靶机网段 | 生产/未授权 IP |
| `authorized_target_only` | in_scope 列表 | 列表外资产 |
| `unrestricted_lab` | 隔离实验网（书面） | 互联网生产 |

## 特色

- 纯 Markdown，**无数据库**  
- 与 `tool-index` / bootstrap 正交：scope 管「能不能打」，tool-index 管「用什么打」

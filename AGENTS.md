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

## 生成文件

- `skills/INDEX.md` 由 `extract-summaries.ps1` 从全部 `SKILL.md` 动态生成，不硬编码模块数量。

## 经验库：模拟器(雷电9)手游内存基址诀窍（2026-11-01 实战沉淀）

场景：宿主侧读 `Ld9BoxHeadless.exe` 拿 guest 手游数据，绕过 guest 内反调试。
工具包已提炼为技能：`skills/emulator-mem/`（通用引擎 `emu/` + 魔域案例 `games/moyu/`，
新游戏按其 SKILL.md 五步适配）；完整接入契约见 `games/moyu/INTEGRATION.md`，
实测工作副本在 `work/moshen/`。

1. **先判 guest 页表模式再动手**：dump 一页疑似表页看高 32 位——
   `hi32 ∈ {0, 0x80000000}` = 32 位 PAE 三级（PDPT 4 条目/32B 对齐）；
   别信文档，文档可能把 x86_64 和 i386 PAE 写混。
2. **只有 PD 页能做 δ 互证**：PD 条目指向 PT（表页，可交叉命中）；
   PT 条目指向数据页（不在表页集合），PT 本身解不出 δ——PT 的物理靠
   "作为 PD 的目标"间接获得。
3. **物理→宿主映射按区域投票**：每个已提交区域 ≈ 一段线性物理映射（一个 δ）。
   真 δ 下目标命中率 ≈100%，用多数票定区域 δ，别信零散的逐页解（噪声多解）。
4. **软打分、勿硬过滤**：锚点/快照存在陈旧比例（环境分钟级漂移），
   硬交集会被第一条陈旧样本误杀真解；对每个候选 δ 统计命中数取最大。
5. **同一稳定窗口纪律**：引擎快照（census/δ）与锚点扫描必须来自同一次运行；
   跨运行混用产物 = 全部失效。定位器必须是"单遍管线"，交付物是
   可重推导的流程+种子文件，不是魔法数字。
6. **回环验证比较同空间地址**：宿主比宿主、物理比物理。
   实体页内偏移（如 +0xAA0）必须在读取时拼回，页基址减字段偏移会跑到前一页。
7. **物理掩码 0x000FFFFFFFFFF000**：用 0xFFFF...F000 会保留 NX 位（bit63），
   叶子地址带脏位做线性映射 → 静默错页。
8. **内核直 map 别名污染一切 qword 扫描**：用户半区条目过滤 hi32==0（严格等于零，
   `hi32 & 0x7FFFFFFF == 0` 放行 NX）。
9. **绕不开就用低一级的链**：读实体只需 PD/PT 两级种子链（真实表页，抗堆抖动），
   pdpt/槽位号可以完全绕开——假窗口问题直接消失。
10. **行为信号比数值匹配可靠**：多候选解析到"同一页内偏移"说明链活着
    （哪怕 base 是错的）；对照值要用新鲜扫描（角色在动，旧 x/y 必失配）。
11. guest 内一切读取手段（/proc/pid/mem、frida）会被 nesec 秒杀；
    宿主侧 ReadProcessMemory 全程无感。多实例按锚点探测选机，RSS 不可靠。

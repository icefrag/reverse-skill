---
name: emulator-mem
description: 雷电9/类VirtualBox安卓模拟器手游的宿主侧内存数据读取工具包。当目标是在模拟器里运行的手游，需要从宿主 Windows 进程直读角色坐标/血量/金币等数据，且 guest 内有反调试（如 libnesec，/proc/mem、frida 注入会秒崩）时使用。核心能力：PAE 页表翻译引擎（客户机物理↔宿主双向解析）、锚点定位、实体 VA 反查、种子链实时读取。不适用于：真机（无宿主进程）、x86_64 guest（引擎是 32 位 PAE 专用，需改造）。
license: MIT
compatibility: Windows 宿主 + Python 3.10+（numpy, psutil）；模拟器默认雷电9（其他 VirtualBox 系可适配，见 SKILL.md 适配节）
allowed-tools: Bash Read Write Edit Glob Grep
metadata:
  user-invocable: "false"
---

# emulator-mem — 模拟器手游宿主侧内存读取

## 方法一句话

游戏数据物理上躺在模拟器宿主进程（如 `Ld9BoxHeadless.exe`）的一块私有内存里。
没有现成的"基址数字"，交付物是一套**可重推导的流程 + 种子文件**：
页表翻译引擎建立 客户机物理↔宿主 映射（δ 互证 + 区域投票）→ 锚点定位实体 →
页表反查实体 VA → 落盘 `{pd_host, pt_host, pdi, pti, heap_delta, page_off}` 种子链 →
读取器两级直连（PT 条目→叶子物理→×堆δ→结构体）。

## 目录结构

```
emu/                    通用引擎（游戏无关，直接复用）
  ldmem.py              宿主读取基座 (OpenProcess/RPM/区域枚举)
  pae.py                PAE 三级页表翻译引擎 (census/δ求解/块表/PDPT索引/va2host)
  entity_scan.py        双槽互证实体扫描 + 遥测提取 (profile 参数化)
games/moyu/             魔域手游案例（雷电9 实测交付，import 已接 emu 包）
  resolve.py            定位器（~5min 冷启动，产出 base_map.json）
  mysj_read.py          单次/循环读取器
  monitor.py            生产监听（多角色跟随/切号/删号/滑动自动恢复）
  INTEGRATION.md        ★ 应用接入文档（数据契约/读取算法/红线）
  BASE-ADDR.md          原理与踩坑记录
```

## 新游戏适配（5 步）

1. **探格式**：dump 一页疑似表页，确认 `hi32 ∈ {0, 0x80000000}`（32 位 PAE）。
   x86_64 guest 需另写四级引擎（本包不覆盖）。
2. **找锚点**：登录后内存里必有一个 int32 稳定值可当角色 ID 锚点
   （遥测 JSON `"role_id":"..."` 是最省事的来源；正则入 profile）。
3. **测绘实体布局**：锚点命中处周边找坐标——多数游戏坐标存两份
   （主槽+副本槽），量出 4 个偏移（主 x/y、副本 x/y）填入 profile
   （`emu/entity_scan.py` 的双槽互证消费它）。
4. **跑定位**：仿照 `games/moyu/resolve.py`，换 profile 跑一遍，
   产出该游戏的 `base_map.json`。
5. **接读取**：`mysj_read.py` 的 FIELDS 表换成新游戏的字段偏移即可，
   恢复层级（L1种子链→L2宿主直读→L3全角色扫描→L4全量重定位）原样复用。

## 硬性红线

- **只读**。写内存有崩溃/封号风险。
- guest 内一切读取（/proc/pid/mem、frida 注入）会被反调试秒杀；
  宿主侧 ReadProcessMemory 全程无感——这是本包存在的理由。
- 物理掩码 `0x000FFFFFFFFFF000`（保留 NX 位的掩码是静默灾难）。
- 读数必须过校验契约（role_id 精确匹配 + 双槽互证 ≤2），失效即走恢复，勿沿用旧值。

## 经验库

11 条实战诀窍（δ互证只对 PD 成立、软打分抗漂移、区域投票、绕不开就降一级等）
见仓库根 `AGENTS.md` §经验库。完整战例复盘见 `games/moyu/BASE-ADDR.md`。

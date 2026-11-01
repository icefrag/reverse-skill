# INTEGRATION — 魔域手游(雷电9模拟器) 实时角色数据读取 · 应用接入文档

> 交付对象: 应用 AI / 上层业务系统
> 所有读取均在 Windows 宿主侧完成 (ReadProcessMemory), 游戏内反调试 (libnesec)
> 无法感知, 实测十几个小时零崩溃。仅限自有 lab / 授权环境使用。
> 最后验证: 2026-11-01, 多角色切换/删号恢复实测通过。

---

## 1. 快速接入 (三个命令)

```bash
cd D:\script-workspace\reverse-skill\work\moshen

python resolve.py        # 定位 (冷启动约5分钟; 换角色/重启模拟器后跑一次)
python mysj_read.py      # 单次读取: role_id/坐标/等级
python monitor.py        # 生产推荐: 弹出控制台实时监听, 自动跟随切号/删号/内存滑动
```

Python 依赖: `numpy`, `psutil` (Python 3.13 实测)。

## 2. 环境事实

| 项 | 值 |
|---|---|
| 模拟器 | 雷电9 (LDPlayer9), 宿主进程 `Ld9BoxHeadless.exe` (可能多开) |
| guest 内核 | **32 位 PAE** Linux 4.4 (三级页表: PDPT→PD→PT), 非 x86_64! |
| 游戏 | 魔域手游 `com.westhouse.mysjb.xsj`, 反调试 libnesec |
| 客户机 RAM | 整块映射在宿主进程私有内存 (本环境 ~3GB @0x7FFF0000 起, 会滑动) |
| 多实例 | 按 role_id 锚点探测选实例 (RSS 启发式不可靠, 见 resolve.pick_game_pid) |

## 3. 数据契约 base_map.json

```json
{
  "pid": 16112,                      // Ld9BoxHeadless 宿主进程
  "role_id": 1006704,                // 当前跟踪角色 (monitor 自动更新)
  "heap_delta": 2095538176,          // 游戏堆 物理地址→宿主地址 线性偏移
  "seeds": {"pd_host": ..., "pt_host": ...},  // 游戏 PD/PT 表页的宿主地址
  "pdi": 189, "pti": 213,            // 实体在 PD/PT 中的条目号
  "page_off": 2720,                  // 实体在 4K 页内偏移 (0xAA0)
  "entity_host": "0x1341dfaa0",      // 实体当前宿主地址 (对照/调试用)
  "pdpt_host": "0xedfeb0e0"          // 备注: 仅供参考, v2 读取不依赖
}
```

## 4. 核心读取算法 (自包含, 可直接移植)

```python
import json, struct, ctypes

def make_reader(base_map_path='base_map.json'):
    bm = json.load(open(base_map_path))
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x0410, False, bm['pid'])          # VM_READ|QUERY
    d, hpd, hpt = bm['heap_delta'], bm['seeds']['pd_host'], bm['seeds']['pt_host']
    pdi, pti, off = bm['pdi'], bm['pti'], bm['page_off']

    def rpm(addr, n):
        buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
        ok = k32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, n, ctypes.byref(got))
        return buf.raw[:got.value] if ok and got.value == n else None

    def read_entity():
        e = rpm(hpt + 8 * pti, 8)                # PT 条目 → 实体所在 4K 页 (物理)
        if not e: return None
        leaf = struct.unpack('<Q', e)[0] & 0x000FFFFFFFFFF000   # 掩码必须清 NX!
        ent = leaf + d + off                     # 堆线性映射 + 页内偏移
        f = {}
        for name, o in [('x',-0x284),('y',-0x280),('x2',0x0AC),('y2',0x0B0),
                        ('level',0x21C),('role_id',0)]:
            r = rpm(ent + o, 4)
            if not r: return None
            f[name] = struct.unpack('<i', r)[0]
        if (f['role_id'] != bm['role_id'] or not 0 < f['x'] < 3000
                or abs(f['x']-f['x2']) > 2 or abs(f['y']-f['y2']) > 2):
            return None                          # 校验失败 = 链路失效/角色不在
        return f
    return read_entity
```

**有效性契约** (应用层必须遵守): `role_id` 精确匹配 + 坐标双槽互证
(±0x284 与 +0xAC 两份拷贝差 ≤2) 同时成立才算有效读数; 任一不成立 → 调用定位流程。

## 5. 可用字段 (场景实体, 已验证)

| 字段 | 偏移 (相对实体) | 类型 | 备注 |
|---|---|---|---|
| x, y | -0x284 / -0x280 | i32 | 瓦片坐标, 与大地图逐位一致 |
| x2, y2 | +0x0AC / +0x0B0 | i32 | 副本槽 (互证用) |
| role_id | +0x000 | i32 | 角色 ID |
| level | +0x21C | i32 | 等级 (1..500) |

### 同仓库已测绘的其他数据 (锚点扫描法, 见 HANDOFF.md §5)

- HP 当前/上限、金币、绑定魔石、魔石、账号 ID、军团 ID(i64) — `moshen_query.py`
- 角色名: 登录遥测 JSON 明文 (`"role_name":"..."`), monitor.scan_names() 已实现

## 6. 失效与恢复

| 症状 | 原因 | 处置 |
|---|---|---|
| 读数 None | 实体页迁移/角色不在场景 | 优先 `monitor.py` (自动 L1-L4 恢复) |
| role_id 校验失败 | 切换了角色 | `resolve.py` 重定位 (monitor 自动) |
| 全部失败且模拟器重启过 | pid 变化 | `resolve.py` (自动选实例) |

monitor.py 恢复层级: L1 VA链直读 → L2 已知位置宿主直读 → L3 全角色扫描+PD重定位
(自动跟随切号, 事件日志打印 `切换角色: 旧→新(名字)`) → L4 全量 resolve (10min 冷却)。

## 7. 事件日志 monitor.log 格式

```
[20:05:16] 切换角色: 1005290(离谱到家) -> 1000783(加西亚-坎洛森)
[20:05:36] 1000783(加西亚-坎洛森) (908,1009) lv=27
[20:04:44] VA 链重接: pdi=189 pti=213 host=0x1341dfaa0
```

## 8. 硬性红线 (违反即崩溃/失效)

1. **只读**。写内存 = 封号/崩溃风险, 且 nesec 有写监控。
2. 不要在 guest 内读 `/proc/<游戏pid>/mem`、不要 frida 注入游戏进程 —
   nesec 秒杀 (历史实测 3/3 崩)。宿主侧 ReadProcessMemory 免疫。
3. 物理地址掩码必须 `0x000FFFFFFFFFF000` (保留 NX 位的掩码是灾难级 bug)。
4. base_map 的 seeds/δ 跨模拟器重启失效; 大区域滑动后可能中途失效 —
   一切以第 4 节校验契约为准, 失效即走恢复, 不要沿用旧读数。

## 9. 文件清单

| 文件 | 角色 |
|---|---|
| `resolve.py` | 定位器: 遥测锚点→堆δ反解→页表反查→种子链落盘 |
| `mysj_read.py` | 单次/循环读取器 (依赖 base_map.json) |
| `monitor.py` | 生产监听: 多角色跟随 + 自动恢复 + 角色名 |
| `pae.py` | PAE 页表翻译引擎 (census/δ求解/块表/索引) |
| `base_map.json` | 数据契约 (§3), monitor 会自动更新 |
| `moshen_query.py` | HP/金币/账号等锚点扫描法读取 (上一期交付) |
| `BASE-ADDR.md` / `HANDOFF.md` / `MEMORY-MAP.md` | 原理/历史/内存结构知识库 |

"""mysj_read.py — 魔域手游 实时坐标读取器 v2 (pd/pt 直连, nesec 无感).

用法:
  python mysj_read.py            # 单次读取
  python mysj_read.py --watch    # 循环读取 (2s 间隔)
  python mysj_read.py --resolve  # base_map 失效时全量重定位

寻址 (v2, 无需 pdpt/窗口): base_map 保存
  seeds.pd_host  游戏进程页目录页 (宿主地址, 真实表页)
  seeds.pt_host  实体所在页表页   (宿主地址)
  pdi / pti      实体在 PD/PT 中的条目号
  heap_delta     游戏堆 物理→宿主 线性δ
  page_off       实体在 4K 页内偏移
读取: PT[pti] → 叶子物理 → ×δ → 宿主 → 结构体字段.
"""
import json
import struct
import sys
import time

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent.parent))
from emu.pae import Pae, PFN_MASK, PS_BIT
from emu.pae import CHUNK, USER_VA_MAX
from emu.ldmem import read as raw_read

FIELDS = {
    'role_id': (0, '<i'),
    'x': (-0x284, '<i'),
    'y': (-0x280, '<i'),
    'x2': (0x0AC, '<i'),
    'y2': (0x0B0, '<i'),
    'level': (0x21C, '<i'),
}


def main():
    watch = '--watch' in sys.argv
    bm = json.load(open('base_map.json'))
    if '--resolve' in sys.argv or 'seeds' not in bm or 'pdi' not in bm:
        print('base_map 缺字段, 重跑定位 ...')
        import resolve
        resolve.main()
        bm = json.load(open('base_map.json'))
    pae = Pae(pid=bm.get('pid') or None)
    rid = bm['role_id']
    d = bm['heap_delta']
    hpd, hpt = bm['seeds']['pd_host'], bm['seeds']['pt_host']
    pdi, pti, off = bm['pdi'], bm['pti'], bm['page_off']

    def rd32(addr):
        r = pae.read(addr, 4)
        return struct.unpack('<i', r)[0] if r and len(r) == 4 else None

    def once():
        # PT[pti] → 叶子物理 (每次现读, 跟踪页迁移)
        r = pae.read(hpt + 8 * pti, 8)
        if not r or len(r) < 8:
            return None
        e = struct.unpack('<Q', r)[0]
        if not (e & 1):
            return None
        leaf = int(e) & int(PFN_MASK)
        base = leaf + d                       # 堆段线性映射
        ent = base + off
        out = {}
        for name, (o, f) in FIELDS.items():
            v = rd32(ent + o)
            if v is None:
                return None
            out[name] = v
        if out['role_id'] != rid or not (0 < out['x'] < 3000 and 0 < out['y'] < 3000) \
                or abs(out['x'] - out['x2']) > 2 or abs(out['y'] - out['y2']) > 2:
            return None
        return out

    r = once()
    if r is None:
        print('!! 种子链失效 (漂移/换角色). 重跑: python resolve.py')
        return
    if not watch:
        print(f"role_id={r['role_id']} 坐标=({r['x']},{r['y']}) 等级={r['level']} "
              f"(双槽校验 Δx={abs(r['x']-r['x2'])} Δy={abs(r['y']-r['y2'])})")
        return
    print('实时坐标 (Ctrl-C 退出):')
    try:
        while True:
            r = once()
            ts = time.strftime('%H:%M:%S')
            if r:
                print(f"\r[{ts}] ({r['x']},{r['y']}) 等级={r['level']}   ",
                      end='', flush=True)
            else:
                print(f"\r[{ts}] 失联 (页迁移, 重试中)   ", end='', flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        print()


if __name__ == '__main__':
    main()

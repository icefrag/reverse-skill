"""entity_scan.py — 通用实体锚点扫描 (双槽互证 + 遥测提取).

双槽互证: 实体结构里坐标存两份 (主槽 + 副本槽), 两份一致才算实体.
对一个新游戏, 只需 profile 给出 4 个偏移 (主槽/副本槽的 x/y) 与值域;
偏移必须 4 字节对齐 (dword).

遥测提取: 登录时游戏向外发送的 JSON 明文, 含 role_id/role_name 等,
正则可配 (不同厂商字段名不同).
"""
import re

import numpy as np

CHUNK = 64 * 1024 * 1024


def find_entity_generic(pae, role_id, prof):
    """双槽互证扫描场景实体.

    prof 字段:
      x_off, y_off      主槽坐标偏移 (相对 role_id 地址, 可负)
      x2_off, y2_off    副本槽偏移
      coord_max         坐标值域上限 (0 < v < coord_max)
      tol               双槽容差 (通常 2)
    返回 [(host, x, y)]
    """
    xa_i = prof['x_off'] // 4
    ya_i = prof['y_off'] // 4
    xb_i = prof['x2_off'] // 4
    yb_i = prof['y2_off'] // 4
    lo_i = min(0, xa_i, ya_i, xb_i, yb_i)
    hi_i = max(0, xa_i + 1, ya_i + 1, xb_i + 1, yb_i + 1)
    vmax = prof.get('coord_max', 3000)
    tol = prof.get('tol', 2)
    hits = []
    for base, size in pae.regions:
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            d = pae.read(base + off, n)
            if not d:
                off += CHUNK
                continue
            ai = np.frombuffer(d[:len(d) // 4 * 4], dtype='<i4')
            m = np.nonzero(ai == np.int32(role_id))[0]
            if len(m):
                m2 = m[(m > -lo_i) & (m < len(ai) - hi_i - 1)]
                if len(m2):
                    xa, ya = ai[m2 + xa_i], ai[m2 + ya_i]
                    xb, yb = ai[m2 + xb_i], ai[m2 + yb_i]
                    ok = ((xa > 0) & (xa < vmax) & (ya > 0) & (ya < vmax)
                          & (xb > 0) & (xb < vmax) & (yb > 0) & (yb < vmax)
                          & (np.abs(xa - xb) <= tol) & (np.abs(ya - yb) <= tol))
                    for i, x, y in zip(m2[ok], xa[ok], ya[ok]):
                        hits.append((base + off + int(i) * 4, int(x), int(y)))
            off += CHUNK
    return hits


def telemetry_scan(pae, patterns):
    """按 profile 的正则收集遥测字段. patterns: {field: bytes_regex}.

    返回 {field: [values...]} (全内存去重收集; 多角色残留都会出现,
    调用方需用 find_entity 逐个验证谁是活角色).
    """
    out = {k: [] for k in patterns}
    comp = {k: re.compile(v) for k, v in patterns.items()}
    for base, size in pae.regions:
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            d = pae.read(base + off, n)
            if not d:
                off += CHUNK
                continue
            for k, c in comp.items():
                for m in c.finditer(d):
                    try:
                        v = int(m.group(1))
                    except ValueError:
                        v = m.group(1)
                    if v not in out[k]:
                        out[k].append(v)
            off += CHUNK
    return out
